#!/usr/bin/env python

import boto3
import cfresources as cfr
import datetime
import json
import os
import string
import time
from upload import upload_lambda, upload_static

def strip(s):
    new_s = ""
    for c in s:
        if c in string.ascii_letters:
            new_s += c
    return new_s

def strip_for_path(s):
    return s.replace(":","").replace("-","")

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

def get_deployer(filename):
    with open(filename,"r") as f:
        api = json.load(f)
    return SunyataDeployer(api=api)

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
        self.stack_name = stack_name if stack_name else "sunyata-{name}".format(name=self.api["name"])
        self.stack_id = None
        self.resources = None
        self._bucket_name = None
        self._static_bucket_name = None
        self._static_url = None
        self.lambda_functions = {}
        self.lambda_keys = {}
        self.static_files = []
        boto3.setup_default_session(region_name=self.api.get("region", "us-east-1"), profile_name=self.api.get("profile", "default"))

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
        for stage in stages:
            self.remove_deployments_for_stage(stage)
        self.combine()
        self._update_stack()
        self.generate()
        self.combine()
        self._update_stack()

    def get_template_from_config(self):
        self.generate()
        self.combine()
        body = self.template
        return self.canonical_template_body(body)

    def get_template_from_cf(self):
        body = self._get_template_body_from_cf()
        return self.canonical_template_body(body)

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
        return self.canonical_api_name(self.api["name"])

    def get_url(self, stage=None):
        stage = stage if stage else self.api["stages"][0]
        return self._get_stack_output("BaseApiUrl") + "/" + stage

    def check_template(self, template_body):
        try:
            boto3.client("cloudformation").validate_template(TemplateBody=template_body)
        except Exception as e:
            print(self.canonical_template_body(template_body))
            raise e

    def _create_stack(self):
        stack = self._get_stack()
        if stack and stack["StackStatus"] != "DELETE_COMPLETE":
            print("Stack {stack_name_or_id} already exists.".format(stack_name_or_id=self.stack_name_or_id))
            return
        cf = boto3.client("cloudformation")
        template_body = self.compact_template_body(self.template)
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
            print("Stack in state {status}.  Waiting 5 seconds.".format(status=status))
            time.sleep(5)
            status = self._get_stack()["StackStatus"]

    def _update_stack(self):
        cf = boto3.client("cloudformation")
        template_body = self.compact_template_body(self.template)
        canonical_old_template = self.canonical_template_body(self._get_template_body_from_cf())
        canonical_new_template = self.canonical_template_body(template_body)
        if canonical_old_template == canonical_new_template:
            print("No update necessary.")
            return
        self.check_template(template_body)
        response = cf.update_stack(
            StackName=self.stack_name_or_id,
            TemplateBody=template_body,
            Capabilities=["CAPABILITY_NAMED_IAM"]
        )
        self.stack_id = response["StackId"]
        status = "UPDATE_IN_PROGRESS"
        while status.endswith("IN_PROGRESS"):
            print("Stack in state {status}.  Waiting 5 seconds.".format(status=status))
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
        resources = boto3.client("cloudformation").describe_stack_resources(StackName=self.stack_name_or_id)["StackResources"]
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
        return self.api["config_path"], configuration

    def _upload_static_files(self):
        for directory in self.api.get("static_dirs",[]):
            self.static_files += upload_static(bucket=self.static_bucket_name, directory=directory)

    def _upload_lambda_code(self):
        bucket = self.lambda_bucket_name
        self.lambda_keys = {}
        config_path, config = self._get_config()
        for function in self.api["lambdas"]:
            key = self.canonical_s3_key(function["name"])
            real_key = upload_lambda(function=function, bucket=bucket, key=key, config_path=config_path, config=config)
            self.lambda_keys[key] = real_key

    def get_current_template_body_from_cf(self):
        return self.canonical_template_body(self._get_template_body_from_cf())

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
            self.cf_infra["StaticFileBucket"] = cfr.bucket(website=True)
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
            name = self.canonical_role_name(raw_name)
            permissions = self.api["roles"][raw_name]
            self.cf_roles[name] = cfr.lambda_role(permissions)

    def generate_functions(self):
        self.lambda_functions = {}
        bucket = self.lambda_bucket_name
        function_arns = []
        for function in self.api["lambdas"]:
            name = function["name"]
            cfname = self.canonical_function_name(name)
            self.lambda_functions[cfname] = function
            function_arns.append({ "Fn::GetAtt" : [cfname, "Arn"]})
            runtime = function["runtime"]
            role = self.canonical_role_name(function["role"])
            handler = function["handler"]
            description = function["description"]
            timeout = function["timeout"]
            memory = function["memory"]
            ckey = self.canonical_s3_key(function["name"])
            key = self.lambda_keys.get(ckey, ckey)
            self.cf_functions[cfname] = cfr.lambda_function(name, runtime, role, handler, description, timeout, memory, bucket, key)
            self.cf_permissions[self.canonical_permissions_name(function["name"])] = cfr.lambda_permission(cfname)
        if self.api.get("stages", None):
            self.cf_roles["APIGWExecRole"] = cfr.apigateway_role(function_arns)

    def generate_models(self):
        models = self.api.get("models", {})
        for model_name in models:
            name = self.canonical_model_name(model_name)
            model = models[model_name]
            self.cf_models[name] = cfr.model(api_name=self.api_name, model_name=name, model=model)

    def generate_resources_and_methods(self):
        paths = self.api["paths"]
        methodmap = {}
        resourceset = set()
        for path in paths:
            pathobj = dict(path)
            pathobj["raw_resource"] = path["path"]
            pathobj["resource"] = self.canonical_resource_name(path["path"])
            pathobj["name"] = self.canonical_method_name(pathobj["function"], pathobj["resource"], pathobj.get("http_method","GET"))
            all_elements = get_expanding_list(pathobj["path"].split("/"))
            for e in all_elements:
                if self.canonical_resource_name(e) != self.canonical_resource_name("/"):
                    resourceset.add(e)
            methodmap[pathobj["name"]] = pathobj
        for resource in resourceset:
            parts = resource.split("/")
            path_part = parts[-1]
            parent_name = self.get_resource_id_for_template(self.canonical_resource_name("/".join(parts[:-1])))
            self.cf_resources[self.canonical_resource_name(resource)] = cfr.resource(path_part, parent_name, self.api_name)
        for name in methodmap:
            method = methodmap[name]
            function_name = self.canonical_function_name(method["function"])
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
                model=self.canonical_model_name(method.get("model")) if method.get("model", None) else None
                )
            if enable_cors:
                self.cf_methods[name + "cors"] = cfr.cors_enabling_method(resource=resource, api_name=self.api_name)

    def generate_deployments(self):
        method_names = self.cf_methods.keys()
        api_name = self.api_name
        stages = self.api.get("stages", [])
        trigger = datetime.datetime.now().strftime("%Y%m%d%H%M")
        for stage in stages:
            name = self.canonical_deployment_name(stage)
            existing_deployment = self.get_logical_resource_from_cf(name)
            #if existing_deployment:
            #    deployment = cfr.deployment(api_name, stage, method_names)
            #    self.cf_stages[self.canonical_stage_name(stage)] = cfr.stage(api_name, stage, existing_deployment["PhysicalResourceId"])
            #    pass
            #else:
            self.cf_deployments[self.canonical_deployment_name(stage)] = cfr.deployment(api_name, stage, method_names)

    def remove_deployments_for_stage(self, stage):
        deployment = self.canonical_deployment_name(stage)
        self.cf_deployments[deployment] = {}
        del self.cf_deployments[deployment]

    def get_resource_id_for_template(self, resource_name):
        if resource_name == "rootResource":
            return { "Fn::GetAtt": [self.api_name, "RootResourceId"] }
        else:
            return { "Ref": resource_name }

    def canonical_deployment_name(self, stage):
        return "{stage}Deployment".format(stage=strip(stage))

    def canonical_stage_name(self, stage):
        return "{stage}Stage".format(stage=strip(stage))

    def canonical_method_name(self, function, resource, http_method):
        return "{function}{resource}{http_method}Method".format(function=strip(function), resource=strip(resource), http_method=strip(http_method))

    def canonical_resource_name(self, path):
        # Remove the forward slashes but have every character following one be capitalized.
        # prepend root
        path = path if path else "/"
        path = path.lower()
        path = path[:-1] if path[-1] == "/" else path
        path = path if not path or path[0] == "/" else "/" + path
        for c in string.ascii_lowercase:
            path = path.replace("/" + c, c.upper())
        if not path:
            return "rootResource"
        return strip(path + "Resource")

    def decanonicalize_resource_name(self, name):
        if name == "rootResource":
            return "/"
        path = name[:-8]
        for c in string.ascii_uppercase:
            path = path.replace(c, "/" + c.lower())
        return path

    def canonical_function_name(self, name):
        return "{name}Function".format(name=strip(name))

    def canonical_permissions_name(self, name):
        return "{name}Permissions".format(name=strip(name))

    def canonical_role_name(self, name):
        return "{name}Role".format(name=strip(name))

    def canonical_api_name(self, name):
        return "{name}API".format(name=strip(name))

    def canonical_model_name(self, name):
        return "{name}Model".format(name=strip(name))

    def canonical_s3_key(self, name):
        return "{name}-lambda-code.zip".format(name=name)

    def canonical_template_body(self, template):
        if type(template) is str:
            return self.canonical_template_body(json.loads(template))
        return json.dumps(template, indent=2, sort_keys=True)

    def compact_template_body(self, template):
        if type(template) is str:
            return self.compact_template_body(json.loads(template))
        return json.dumps(template, separators=(',',':'), sort_keys=True)

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
