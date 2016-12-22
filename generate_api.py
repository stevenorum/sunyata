#!/usr/bin/env python

import boto3
import cfresources as cfr
import json
import os
import string
import time
from upload import upload_lambda

def strip(s):
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

class SunyataDeployer(object):
    cf_infra = {}
    cf_apis = {}
    cf_roles = {}
    cf_functions = {}
    cf_permissions = {}
    cf_deployments = {}
    cf_resources = {}
    cf_methods = {}
    cf_outputs = {}

    def __init__(self, api, stack_name=None):
        self.api = api
        self.stack_name = stack_name if stack_name else "sunyata-{name}".format(name=self.api["name"])
        self.stack_id = None
        self.resources = None
        self._bucket_name = None
        boto3.setup_default_session(region_name=self.api.get("region", "us-east-1"), profile_name=self.api.get("profile", "default"))

    @property
    def stack_name_or_id(self):
        return self.stack_id if self.stack_id else self.stack_name

    @property
    def bucket_name(self):
        self._bucket_name = self._bucket_name if self._bucket_name else self._get_stack_output("LambdaZipBucket")
        return self._bucket_name

    @property
    def api_name(self):
        return self.canonical_api_name(self.api["name"])

    def deploy_initial(self):
        self.clear_analysis()
        self.generate_infra()
        self.combine()
        self._deploy(create=True, update=False, fail=False)
        self._upload_lambda_code()
        self.deploy()

    def deploy(self):
        self.generate()
        self.combine()
        self._deploy(create=False, update=True, fail=False)

    def get_url(self, stage=None):
        stage = stage if stage else self.api["stages"][0]
        return self._get_stack_output("BaseApiUrl") + "/" + stage

    def _deploy(self, create=False, update=True, fail=True):
        if not self.template:
            print("No CF template found.  Perhaps you haven't yet called SunyataDeployer.combine(), or you've called SunyataDeployer.clear_analysis() since the last combine call?")
            raise RuntimeError("CF template not found.")
        #print(json.dumps(self.template, indent=2, sort_keys=True))
        stack = self._get_stack()
        if create:
            if stack:
                pass
            else:
                self._create_stack()
        else:
            if fail and not stack:
                raise RuntimeError("Stack doesn't exist and create is false.")
        if update:
            if not stack:
                pass
            else:
                self._update_stack()
        else:
            if fail and stack:
                raise RuntimeError("Stack exists and update is false.")

    def _create_stack(self):
        stack = self._get_stack()
        if stack and stack["StackStatus"] != "DELETE_COMPLETE":
            print("Stack {stack_name_or_id} already exists.".format(stack_name_or_id=self.stack_name_or_id))
            return
        cf = boto3.client("cloudformation")
        template_body = self.compact_template_body(self.template)
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

    def _get_template_body_from_cf(self):
        return boto3.client("cloudformation").get_template(StackName=self.stack_name_or_id)["TemplateBody"]

    def _get_stack_output(self, key):
        stack = self._get_stack()
        matching = [o["OutputValue"] for o in stack["Outputs"] if o["OutputKey"] == key]
        if matching:
            return matching[0]
        return None

    def _upload_lambda_code(self):
        bucket = self.bucket_name
        for function in self.api["lambdas"]:
            key = self.canonical_s3_key(function["name"])
            upload_lambda(function=function, bucket=bucket, key=key)

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
        self.resources = None
        self.template = None

    def generate(self):
        self.clear_analysis()
        self.generate_infra()
        self.generate_apis()
        self.generate_roles()
        self.generate_functions()
        self.generate_resources_and_methods()
        self.generate_deployments()

    def generate_infra(self):
        self.cf_infra["LambdaZipBucket"] = cfr.bucket()
        self.cf_outputs["LambdaZipBucket"] = {"Value" : {"Ref" : "LambdaZipBucket"}}

    def generate_apis(self):
        api_name = self.api_name
        self.cf_apis[api_name] = cfr.api(api_name)
        self.cf_outputs["BaseApiUrl"] = {"Value" : { "Fn::Join" : [ "", [ "https://",{"Ref" : api_name},".execute-api.",{"Ref" : "AWS::Region"},".amazonaws.com"] ] }}

    def generate_roles(self):
        for raw_name in self.api["roles"]:
            name = self.canonical_role_name(raw_name)
            permissions = self.api["roles"][raw_name]
            self.cf_roles[name] = cfr.lambda_role(permissions)

    def generate_functions(self):
        bucket = self.bucket_name
        function_arns = []
        for function in self.api["lambdas"]:
            name = function["name"]
            cfname = self.canonical_function_name(name)
            function_arns.append({ "Fn::GetAtt" : [cfname, "Arn"]})
            runtime = function["runtime"]
            role = self.canonical_role_name(function["role"])
            handler = function["handler"]
            description = function["description"]
            timeout = function["timeout"]
            memory = function["memory"]
            key = self.canonical_s3_key(function["name"])
            self.cf_functions[cfname] = cfr.lambda_function(name, runtime, role, handler, description, timeout, memory, bucket, key)
            self.cf_permissions[self.canonical_permissions_name(function["name"])] = cfr.lambda_permission(cfname)
        self.cf_roles["APIGWExecRole"] = cfr.apigateway_role(function_arns)

    def generate_resources_and_methods(self):
        paths = self.api["paths"]
        methodmap = {}
        resourceset = set()
        for path in paths:
            pathobj = dict(path)
            pathobj["resource"] = self.canonical_resource_name(path["path"])
            pathobj["name"] = self.canonical_method_name(pathobj["function"], pathobj["resource"])
            all_elements = get_expanding_list(pathobj["path"].split("/"))
            for element in [self.canonical_resource_name(e) for e in all_elements]:
                if element != self.canonical_resource_name("/"):
                    resourceset.add(element)
            methodmap[pathobj["name"]] = pathobj
        for resource in resourceset:
            parts = self.decanonicalize_resource_name(resource).split("/")
            path_part = parts[-1]
            parent_name = self.get_resource_id_for_template(self.canonical_resource_name("/".join(parts[:-1])))
            self.cf_resources[resource] = cfr.resource(path_part, parent_name, self.api_name)
        for name in methodmap:
            method = methodmap[name]
            function_name = self.canonical_function_name(method["function"])
            resource = self.get_resource_id_for_template(method["resource"])
            self.cf_methods[name] = cfr.method(function_name, resource, self.api_name, method.get("querystring_params",{}), method.get("extra",{}))

    def generate_deployments(self):
        method_names = self.cf_methods.keys()
        api_name = self.api_name
        stages = self.api.get("stages", ["alpha"])
        for stage in stages:
            self.cf_deployments[self.canonical_deployment_name(stage)] = cfr.deployment(api_name, stage, method_names)

    def get_resource_id_for_template(self, resource_name):
        if resource_name == "rootResource":
            return { "Fn::GetAtt": [self.api_name, "RootResourceId"] }
        else:
            return { "Ref": resource_name }

    def canonical_deployment_name(self, stage):
        return "{stage}Deployment".format(stage=strip(stage))

    def canonical_method_name(self, function, resource):
        return "{function}{resource}Method".format(function=strip(function), resource=strip(resource))

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
        return path + "Resource"

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
        self.resources.update(self.cf_resources)
        self.resources.update(self.cf_deployments)
        self.resources.update(self.cf_permissions)
        self.resources.update(self.cf_functions)
        self.resources.update(self.cf_roles)
        self.resources.update(self.cf_infra)
        self.template = cfr.overall_template(
            resources=self.resources,
            outputs=self.cf_outputs,
            description=self.api["description"]
        )

template_file = "simpleapi.json"
with open(template_file,"r") as f:
    api = json.load(f)

deployer = SunyataDeployer(api, "SunyataTestStack")
deployer.deploy_initial()
print(deployer.get_current_template_body_from_cf())
print(deployer.get_url())
