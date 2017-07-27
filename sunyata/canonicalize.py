#!/usr/bin/env python3

import json
import string

API_NAME=""

def set_api(api):
    global API_NAME
    API_NAME=_strip(api)

def _strip(s):
    new_s = ""
    for c in s:
        if c in string.ascii_letters:
            new_s += c
    return new_s

def strip_for_path(s):
    return s.replace(":","").replace("-","")

def strip(function):
    if type(function) == str:
        return _strip(function)
    def stripper(*args, **kwargs):
        _args = [_strip(arg) if arg else arg for arg in args]
        _kwargs = {k:_strip(kwargs[k]) if kwargs[k] else kwargs[k] for k in kwargs}
        return function(*_args, **_kwargs)
    return stripper

def _prefixAPI(s):
    if s:
        return API_NAME + s
    else:
        return s

def prefixAPI(function):
    if type(function) == str:
        return _prefixAPI(function)
    def prefixer(*args, **kwargs):
        return _prefixAPI(function(*args, **kwargs))
    return prefixer

@strip
@prefixAPI
def canonical_bucket_name(name):
    return name

@strip
def canonical_bucket_policy_name(name):
    return canonical_bucket_name(name)+"Policy"

@strip
@prefixAPI
def canonical_deployment_name(stage):
    return "{stage}Deployment".format(stage=stage)

@strip
@prefixAPI
def canonical_mapping_name(stage):
    return "{stage}Mapping".format(stage=stage)

@strip
@prefixAPI
def canonical_stage_name(stage):
    return "{stage}Stage".format(stage=stage)

@strip
@prefixAPI
def canonical_method_name(function, resource, http_method):
    return "{function}{resource}{http_method}Method".format(function=function, resource=resource, http_method=http_method)

def canonical_resource_name(path):
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
    return _strip(path) + "Resource"

def decanonicalize_resource_name(name):
    if name == "rootResource":
        return "/"
    path = name[:-8]
    for c in string.ascii_uppercase:
        path = path.replace(c, "/" + c.lower())
    return path

@strip
@prefixAPI
def canonical_function_name(name):
    return "{name}Function".format(name=name)

@strip
@prefixAPI
def canonical_permissions_name(name):
    return "{name}Permissions".format(name=name)

@strip
@prefixAPI
def canonical_role_name(name):
    return "{name}Role".format(name=name)

@strip
@prefixAPI
def canonical_api_name(name):
    return "API"

@strip
@prefixAPI
def canonical_model_name(name):
    return "{name}Model".format(name=name)

@strip # As this deals in filepaths, it is possible for strip to create ambiguity if you have some lambdas from directory foo/bar/baz and some from directory foob/arbaz, but if you're doing that your code is bad and you should feel bad.
def canonical_s3_key(file=None, directory=None):
    if not file and not directory:
        raise RuntimeError("Must specify either file or directory for each lambda function.")
    elif file and directory:
        raise RuntimeError("Must specify either file or directory for each lambda function, not both.")
    elif file:
        return "file-{file}-lambda.zip".format(file=file)
    else:
        return "dir-{directory}-lambda.zip".format(directory=directory)

def canonical_template_body(template):
    if type(template) is str:
        return canonical_template_body(json.loads(template))
    return json.dumps(template, indent=2, sort_keys=True)

def compact_template_body(template):
    if type(template) is str:
        return compact_template_body(json.loads(template))
    return json.dumps(template, separators=(',',':'), sort_keys=True)
