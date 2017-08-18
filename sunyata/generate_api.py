#!/usr/bin/env python3

import boto3
from botocore.exceptions import ClientError
from sunyata import canonicalize
from sunyata import cfresources as cfr
import datetime
import json
import logging
import os
import time
from sunyata.upload import upload_lambda, upload_static

def path_joiner(parent, child):
    if not parent:
        return child
    return os.path.join(parent, child)

def get_expanding_list(l, joiner=path_joiner):
    parent = None
    all_lists = []
    for child in l:
        joint = joiner(parent, child)
        all_lists.append(joint)
        parent = joint
    return all_lists

def load_template(filename):
    with open(filename,"r") as f:
        lines = f.readlines()
        template = json.loads("\n".join([l for l in lines if not l.startswith("#")]))
        pass
    configuration = {}
    for fname in template.get("inherits_from", []):
        fpath = os.path.join(os.path.dirname(filename), fname)
        configuration.update(load_template(fpath))
        pass
    configuration.update(template)
    return configuration

def merge_templates(filenames):
    configs = [load_template(fname) for fname in filenames]
    merged_config = {}
    for config in configs:
        merged_config.update(config)
    return merged_config

def get_deployer(filenames):
    return SunyataDeployer(api=merge_templates(filenames))

def get_content_type(path):
    if path.endswith(".html"):
        return "text/html"
    if path.endswith(".css"):
        return "text/css"
    return "text/html"

class SunyataDeployer(object):
    cf_infra = {}
    cf_apis = {}
    cf_roles = {}
    cf_functions = {}
    cf_permissions = {}
    cf_deployments = {}
    cf_stages = {}
    cf_resources = {}
    cf_methods = {}
    cf_models = {}
    cf_outputs = {}

    def __init__(self, api, stack_name=None):
        self.api = api
        self.stage_config = self.api.get("stage_config", {})
        self.stack_name = stack_name if stack_name else "sunyata-{name}".format(name=self.api["name"])
        canonicalize.set_api(self.api["name"])
        self.stack_id = None
        self.resources = None
        self._bucket_name = None
        self._static_bucket_name = None
        self._static_url = None
        self.lambda_functions = {}
        self.lambda_keys = {}
        self.static_files = []
        self.domain = self.api.get("domain_name", None)
        self.extra_cf_templates = self.api.get("extra_cloudformation_templates", [])
        self.region = self.api.get("region", "us-east-1")
        boto3.setup_default_session(region_name=self.region, profile_name=self.api.get("profile", "default"))

    ##### begin externally-used methods #####

    def deploy(self):
        self.generate()
        self.combine()
        stack = self._get_stack()
        create = not stack
        if create:
            self.deploy_initial()
        else:
            self.redeploy_to_stages(self.api["stages"])

    def deploy_initial(self):
        if self._get_stack():
            raise RuntimeError("Stack already exists!")
        self.clear_analysis()
        self.generate_infra()
        self.combine()
        self._create_stack()
        self._upload_static_files()
        self._upload_lambda_code()
        self.generate()
        self.combine()
        self._update_stack()

    def redeploy_to_stages(self, stages=None):
        if not self._get_stack():
            raise RuntimeError("Stack doesn't exist!")
        stages = self.api["stages"] if stages==None else stages
        self._upload_static_files()
        self._upload_lambda_code()
        self.generate()
        # for stage in stages:
        #     self.remove_deployments_for_stage(stage)
        # self.combine()
        # self._update_stack()
        self.generate()
        self.combine()
        self._update_stack()

    def get_template_from_config(self):
        self.generate()
        self.combine()
        body = self.template
        return canonicalize.canonical_template_body(body)

    def get_template_from_cf(self):
        body = self._get_template_body_from_cf()
        return canonicalize.canonical_template_body(body)

    ##### end externally-used methods #####

    @property
    def stack_name_or_id(self):
        return self.stack_id if self.stack_id else self.stack_name

    @property
    def lambda_bucket_name(self):
        self._bucket_name = self._bucket_name if self._bucket_name else self._get_stack_output("LambdaZipBucket")
        return self._bucket_name

    @property
    def static_bucket_name(self):
        self._static_bucket_name = self._static_bucket_name if self._static_bucket_name else self._get_stack_output("StaticFileBucket")
        return self._static_bucket_name

    @property
    def static_s3_path(self):
        self._static_url = self._static_url if self._static_url else self._get_stack_output("StaticURL")
        return self._static_url

    @property
    def api_name(self):
        return canonicalize.canonical_api_name(self.api["name"])

    def get_url(self, stage=None):
        stage = stage if stage else self.api["stages"][0]
        base_url = self._get_stack_output("BaseApiUrl")
        base_url = base_url if base_url else ""
        return base_url + "/" + stage

    def check_template(self, template_body):
        try:
            boto3.client("cloudformation").validate_template(TemplateBody=template_body)
        except Exception as e:
            logging.exception(canonicalize.canonical_template_body(template_body))
            raise e

    def _delete_resource(self, resource):
        type = resource["Type"]
        service = type.split("::")[1]
        if type == "AWS::ApiGateway::BasePathMapping":
            logging.info("Manually deleting AWS::ApiGateway::BasePathMapping resource.")
            logging.debug(boto3.client("apigateway").delete_base_path_mapping(domainName=resource["Properties"]["DomainName"], basePath=resource["Properties"]["BasePath"] if resource["Properties"]["BasePath"] else '""'))
        else:
            raise RuntimeError("Sunyata doesn't know how to delete resource type {type}".format(type=type))

    def _handle_manual_pre_transition_steps(self, old_template, new_template):
        logging.info("Looking for pre-transition steps that need to be handled manually.")
        old_template = json.loads(old_template)
        new_template = json.loads(new_template)
        old_resources = old_template["Resources"]
        new_resources = new_template["Resources"]
        resources_cf_fucks_up = ["AWS::ApiGateway::BasePathMapping"]
        for resource in old_resources:
            if resource not in new_resources and old_resources[resource]["Type"] in resources_cf_fucks_up:
                self._delete_resource(old_resources[resource])

    def _create_stack(self):
        stack = self._get_stack()
        if stack and stack["StackStatus"] != "DELETE_COMPLETE":
            logging.warn("Stack {stack_name_or_id} already exists.".format(stack_name_or_id=self.stack_name_or_id))
            return
        cf = boto3.client("cloudformation")
        template_body = canonicalize.compact_template_body(self.template)
        self.check_template(template_body)
        response = cf.create_stack(
            StackName=self.stack_name,
            TemplateBody=template_body,
            Capabilities=["CAPABILITY_NAMED_IAM"],
            DisableRollback=True
        )
        self.stack_id = response["StackId"]
        status = "CREATE_IN_PROGRESS"
        while status.endswith("IN_PROGRESS"):
            if status in ["CREATE_IN_PROGRESS", "UPDATE_IN_PROGRESS"]:
                logging.debug("Stack in state {status}.  Waiting 5 seconds.".format(status=status))
            else:
                logging.info("Stack in state {status}.  Waiting 5 seconds.".format(status=status))
            time.sleep(5)
            status = self._get_stack()["StackStatus"]

    def _same_resource_names(self, old_template, new_template):
        old_stack = old_template if type(old_template) == dict else json.loads(old_template)
        new_stack = new_template if type(new_template) == dict else json.loads(new_template)
        return ",".join(sorted(old_stack["Resources"].keys())) == ",".join(sorted(new_stack["Resources"].keys()))

    def _update_stack(self):
        cf = boto3.client("cloudformation")
        template_body = canonicalize.compact_template_body(self.template)
        canonical_old_template = canonicalize.canonical_template_body(self._get_template_body_from_cf())
        canonical_new_template = canonicalize.canonical_template_body(template_body)
#         if canonical_old_template == canonical_new_template:
#             logging.info("No update necessary.")
#             return
        # if self._same_resource_names(canonical_old_template, canonical_new_template):
        #     logging.info("Highly likely (but not fully guaranteed) that no update is necessary.")
        #     return
        self.check_template(template_body)
#         self._handle_manual_pre_transition_steps(old_template=canonical_old_template, new_template=canonical_new_template)
        response = cf.update_stack(
            StackName=self.stack_name_or_id,
            TemplateBody=template_body,
            Capabilities=["CAPABILITY_NAMED_IAM"]
        )
        self.stack_id = response["StackId"]
        status = "UPDATE_IN_PROGRESS"
        while status.endswith("IN_PROGRESS"):
            if status in ["CREATE_IN_PROGRESS", "UPDATE_IN_PROGRESS"]:
                logging.debug("Stack in state {status}.  Waiting 5 seconds.".format(status=status))
            else:
                logging.info("Stack in state {status}.  Waiting 5 seconds.".format(status=status))
            time.sleep(5)
            status = self._get_stack()["StackStatus"]

    def _get_stack(self):
        try:
            stacks = boto3.client("cloudformation").describe_stacks(StackName=self.stack_name_or_id)["Stacks"]
            if stacks:
                return stacks[0]
        except Exception as e:
            pass
        return None

    def get_logical_resource_from_cf(self, logical_name):
        try:
            resources = boto3.client("cloudformation").describe_stack_resources(StackName=self.stack_name_or_id)["StackResources"]
        except ClientError as e:
            return None
        resources = [r for r in resources if r["LogicalResourceId"] == logical_name]
        if resources:
            return resources[0]
        else:
            return None

    def get_deployments_from_cf(self):
        resources = boto3.client("cloudformation").describe_stack_resources(StackName=self.stack_name_or_id)["StackResources"]
        deployments = [r for r in resources if r["ResourceType"]=="AWS::ApiGateway::Deployment"]
        return deployments

    def _get_template_body_from_cf(self):
        return boto3.client("cloudformation").get_template(StackName=self.stack_name_or_id)["TemplateBody"]

    def _get_stack_output(self, key):
        stack = self._get_stack()
        if not stack:
            return None
        matching = [o["OutputValue"] for o in stack["Outputs"] if o["OutputKey"] == key]
        if matching:
            return matching[0]
        return None

    def _get_config(self):
        if not self.api.get("config_path", None):
            return None, {}
        configuration = {}
        configuration["static_file_url"] = self.static_s3_path
        configuration["static_file_list"] = self.static_files
        configuration["static_file_bucket"] = self.static_bucket_name
        configuration["base_url"] = self.get_url()
        configuration["aws_account_id"] = boto3.client('sts').get_caller_identity().get('Account')
        configuration["aws_region"] = self.region
        configuration.update(self.stage_config)
        return self.api["config_path"], configuration

    def _upload_static_files(self):
        for directory in self.api.get("static_dirs",[]):
            self.static_files += upload_static(bucket=self.static_bucket_name, directory=directory)

    def _upload_lambda_code(self):
        bucket = self.lambda_bucket_name
        self.lambda_keys = {}
        config_path, config = self._get_config()
        real_keys = {}
        for function in self.api["lambdas"]:
            key = canonicalize.canonical_s3_key(file=function.get("file", None), directory=function.get("directory", None))
            if not key in real_keys:
                logging.info("Uploading bundle {key}".format(key=key))
                real_keys[key] = upload_lambda(function=function, bucket=bucket, key=key, config_path=config_path, config=config)
            else:
                logging.info("Bundle {key} already uploaded.  Skipping.".format(key=key))
            self.lambda_keys[key] = real_keys[key]

    def get_current_template_body_from_cf(self):
        return canonicalize.canonical_template_body(self._get_template_body_from_cf())

    def clear_analysis(self):
        self.cf_infra = {}
        self.cf_apis = {}
        self.cf_roles = {}
        self.cf_functions = {}
        self.cf_permissions = {}
        self.cf_deployments = {}
        self.cf_resources = {}
        self.cf_methods = {}
        self.cf_models = {}
        self.resources = None
        self.template = None

    def generate(self):
        self.clear_analysis()
        self.generate_infra()
        self.generate_apis()
        self.generate_roles()
        self.generate_functions()
        self.generate_models()
        self.generate_resources_and_methods()
        self.generate_deployments()

    def generate_infra(self):
        self.cf_infra["LambdaZipBucket"] = cfr.bucket()
        self.cf_outputs["LambdaZipBucket"] = {"Value" : {"Ref" : "LambdaZipBucket"}}
        if self.api.get("static_dirs", None):
            self.cf_infra["StaticFileBucket"] = cfr.bucket(cors=True,website=True)
            self.cf_infra["StaticFileBucketPolicy"] = cfr.public_bucket_policy("StaticFileBucket")
            self.cf_outputs["StaticURL"] = {"Value" : {"Fn::GetAtt":["StaticFileBucket","WebsiteURL"]}}
            self.cf_outputs["StaticFileBucket"] = {"Value" : {"Ref" : "StaticFileBucket"}}

    def generate_apis(self):
        api_name = self.api_name
        if self.api.get("stages", None):
            self.cf_apis[api_name] = cfr.api(api_name)
            self.cf_outputs["BaseApiUrl"] = {"Value" : { "Fn::Join" : [ "", [ "https://",{"Ref" : api_name},".execute-api.",{"Ref" : "AWS::Region"},".amazonaws.com"] ] }}

    def generate_roles(self):
        for raw_name in self.api["roles"]:
            name = canonicalize.canonical_role_name(raw_name)
            permissions = self.api["roles"][raw_name]
            self.cf_roles[name] = cfr.lambda_role(permissions)

    def generate_functions(self):
        self.lambda_functions = {}
        bucket = self.lambda_bucket_name
        function_arns = []
        for function in self.api["lambdas"]:
            name = function["name"]
            cfname = canonicalize.canonical_function_name(name)
            self.lambda_functions[cfname] = function
            function_arns.append({ "Fn::GetAtt" : [cfname, "Arn"]})
            runtime = function["runtime"]
            role = canonicalize.canonical_role_name(function["role"])
            handler = function["handler"]
            description = function["description"]
            timeout = function["timeout"]
            memory = function["memory"]
            vpc_config = function.get("vpc_config", None)
            ckey = canonicalize.canonical_s3_key(file=function.get("file", None), directory=function.get("directory", None))
            key = self.lambda_keys.get(ckey, ckey)
            self.cf_functions[cfname] = cfr.lambda_function(name, runtime, role, handler, description, timeout, memory, bucket, key, vpc_config=vpc_config)
            self.cf_permissions[canonicalize.canonical_permissions_name(function["name"])] = cfr.lambda_permission(cfname)
        if self.api.get("stages", None):
            self.cf_roles["APIGWExecRole"] = cfr.apigateway_role(function_arns)

    def generate_models(self):
        models = self.api.get("models", {})
        for model_name in models:
            name = canonicalize.canonical_model_name(model_name)
            model = models[model_name]
            self.cf_models[name] = cfr.model(api_name=self.api_name, model_name=name, model=model)

    def generate_resources_and_methods(self):
        paths = self.api["paths"]
        methodmap = {}
        resourceset = set()
        for path in paths:
            pathobj = dict(path)
            pathobj["raw_resource"] = path["path"]
            pathobj["resource"] = canonicalize.canonical_resource_name(path["path"])
            pathobj["name"] = canonicalize.canonical_method_name(pathobj["function"], pathobj["resource"], pathobj.get("http_method","GET"))
            all_elements = get_expanding_list(pathobj["path"].split("/"))
            for e in all_elements:
                if canonicalize.canonical_resource_name(e) != canonicalize.canonical_resource_name("/"):
                    resourceset.add(e)
            methodmap[pathobj["name"]] = pathobj
        for resource in resourceset:
            parts = resource.split("/")
            path_part = parts[-1]
            parent_name = self.get_resource_id_for_template(canonicalize.canonical_resource_name("/".join(parts[:-1])))
            self.cf_resources[canonicalize.canonical_resource_name(resource)] = cfr.resource(path_part, parent_name, self.api_name)
        for name in methodmap:
            method = methodmap[name]
            function_name = canonicalize.canonical_function_name(method["function"])
            resource = self.get_resource_id_for_template(method["resource"])
            content_type = get_content_type(method["raw_resource"])
            proxy = self.lambda_functions[function_name].get("proxy", False)
            integration_type = "AWS_PROXY" if proxy else "AWS"
            http_method = method.get("http_method","GET")
            enable_cors = method.get("enable_cors", False)
            self.cf_methods[name] = cfr.method(
                function_name=function_name,
                resource=resource,
                api_name=self.api_name,
                content_type=content_type,
                querystring_params=method.get("querystring_params",{}),
                extra=method.get("extra",{}),
                integration_type=integration_type,
                http_method=http_method,
                enable_cors=enable_cors,
                model=canonicalize.canonical_model_name(method.get("model")) if method.get("model", None) else None
                )
            if enable_cors:
                self.cf_methods[name + "cors"] = cfr.cors_enabling_method(resource=resource, api_name=self.api_name)

    def generate_deployments(self):
        method_names = [k for k in self.cf_methods.keys()]
        api_name = self.api_name
        stages = self.api.get("stages", [])
        trigger = datetime.datetime.now().strftime("%Y%m%d%H%M")
        for stage in stages:
            name = canonicalize.canonical_deployment_name(stage)
            existing_deployment = self.get_logical_resource_from_cf(name)
#             if existing_deployment:
#                deployment = cfr.deployment(api_name, stage, method_names)
#                self.cf_stages[canonicalize.canonical_stage_name(stage)] = cfr.stage(api_name, stage, existing_deployment["PhysicalResourceId"])
#             else:
            self.cf_deployments[canonicalize.canonical_deployment_name(stage)] = cfr.deployment(api_name, stage, method_names)
            if self.domain:
                prefix = self.api.get("stage_mapping", {}).get(stage,None)
                prefix = prefix if prefix != None else stage
                self.cf_deployments[canonicalize.canonical_mapping_name(stage)] = cfr.api_domain_mapping(domain=self.domain, api_name=self.api_name, base_path=prefix, stage=stage, depends_on=name)

    def remove_deployments_for_stage(self, stage):
        deployment = canonicalize.canonical_deployment_name(stage)
        self.cf_deployments[deployment] = {}
        self.cf_deployments[canonicalize.canonical_mapping_name(stage)] = {}
        del self.cf_deployments[deployment]
        del self.cf_deployments[canonicalize.canonical_mapping_name(stage)]

    def get_resource_id_for_template(self, resource_name):
        if resource_name == "rootResource":
            return { "Fn::GetAtt": [self.api_name, "RootResourceId"] }
        else:
            return { "Ref": resource_name }

    def combine(self):
        self.resources = self.cf_apis.copy()
        self.resources.update(self.cf_methods)
        self.resources.update(self.cf_models)
        self.resources.update(self.cf_resources)
        self.resources.update(self.cf_deployments)
        self.resources.update(self.cf_stages)
        self.resources.update(self.cf_permissions)
        self.resources.update(self.cf_functions)
        self.resources.update(self.cf_roles)
        self.resources.update(self.cf_infra)
        for extra_template in self.extra_cf_templates:
            with open(extra_template, 'r') as f:
                template = json.load(f)
                # TODO: add support for parameters and outputs.
                self.resources.update(template["Resources"])
        self.template = cfr.overall_template(
            resources=self.resources,
            outputs=self.cf_outputs,
            description=self.api["description"]
        )

#template_file = "simpleapi.json"
#with open(template_file,"r") as f:
#    api = json.load(f)

#deployer = SunyataDeployer(api, "SunyataTestStack")
#deployer.deploy()
#deployer.redeploy_to_stage("alpha")
#print(deployer.get_current_template_body_from_cf())
#print(deployer.get_url())
