#!/usr/bin/env python

import json

DEFAULT_NAME = "sunyata"
false = False
true = True
null = None

def bucket(name=None):
    bucket_template = {
        "Type" : "AWS::S3::Bucket",
        "Properties" : {}
    }
    if name:
        bucket_template["Properties"]["BucketName"] = name
    return bucket_template

def api(name=DEFAULT_NAME, description=None):
    description = description if description else "The ApiGateway API for " + name
    api_template = {
        "Type" : "AWS::ApiGateway::RestApi",
        "Properties" : {
            "Description" : description,
            "Name" : name,
            "Parameters" : {}
        }
    }
    return api_template

def lambda_permission(cfname):
    permission_template = {
        "Type": "AWS::Lambda::Permission",
        "Properties": {
            "FunctionName" : { "Fn::GetAtt" : [cfname, "Arn"] },
            "Action": "lambda:InvokeFunction",
            "Principal": "apigateway.amazonaws.com",
            "SourceAccount": { "Ref" : "AWS::AccountId" }
        }
    }
    return permission_template

def lambda_function(name, runtime, role, handler, description, timeout, memory, bucket, key):
    function_template = {
        "Type" : "AWS::Lambda::Function",
        "Properties" : {
            "Code" : {
                "S3Bucket" : bucket,
                "S3Key" : key
            },
            "Description" : description,
            "Environment" : {
                "Variables" : {}
            },
            "FunctionName" : name,
            "Handler" : handler,
            "MemorySize" : memory,
            "Role" : { "Fn::GetAtt" : [role, "Arn"] },
            "Runtime" : runtime,
            "Timeout" : timeout
        }
    }
    return function_template

def apigateway_role(function_arns):
    role_template = {
        "Type" : "AWS::IAM::Role",
        "Properties" : {
            "AssumeRolePolicyDocument": {
                'Version': '2012-10-17',
                'Statement': [
                    {
                        'Effect': 'Allow',
                        'Principal': {'Service': ['apigateway.amazonaws.com']},
                        'Action': ['sts:AssumeRole']
                    }
                ]
            },
            "Path": "/",
            "Policies": [
                {
                    'PolicyName': 'root',
                    'PolicyDocument': {
                        'Version': '2012-10-17',
                        'Statement': [
                        {
                            "Effect": "Allow",
                            "Action": ["lambda:InvokeFunction"],
                            "Resource": function_arns
                        },
                        {
                            "Effect": "Allow",
                            "Action": ["iam:PassRole"],
                            "Resource": "*"
                        }
                    ]
                    }
                }
            ]
        }
    }
    return role_template

def lambda_role(permissions=[]):
    role_template = {
        "Type" : "AWS::IAM::Role",
        "Properties" : {
            "AssumeRolePolicyDocument": {
                'Version': '2012-10-17',
                'Statement': [
                    {
                        'Effect': 'Allow',
                        'Principal': {'Service': ['lambda.amazonaws.com']},
                        'Action': ['sts:AssumeRole']
                    }
                ]
            },
            "Path": "/",
            "Policies": [
                {
                    'PolicyName': 'root',
                    'PolicyDocument': {
                        'Version': '2012-10-17',
                        'Statement': permissions
                    }
                }
            ]
        }
    }
    return role_template

def overall_template(resources={}, parameters={}, outputs={}, description="sunyata CloudFormation stack"):
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Parameters" : parameters,
        "Resources": resources,
        "Outputs": outputs,
        "Description": description
    }

def resource(path_part, parent_name, api_name):
    resource_template = {
        "Type" : "AWS::ApiGateway::Resource",
        "Properties" : {
            "ParentId" : parent_name,
            "PathPart" : path_part,
            "RestApiId" : {"Ref": api_name}
        }
    }
    return resource_template

def method(function_name, resource, api_name, querystring_params={}, extra={}):
    RequestTemplate = {}
    RequestParameters = {}
    for url_param in querystring_params:
        event_param = querystring_params[url_param]
        RequestTemplate[event_param] = "$input.params('{url_param}')".format(url_param=url_param)
        RequestParameters["method.request.querystring." + url_param] = False
    RequestTemplate.update(extra)
    rt_string = json.dumps(json.dumps(RequestTemplate,separators=(',',':')))
    method_template = {
                "Type" : "AWS::ApiGateway::Method",
                "Properties" : {
                    "ApiKeyRequired": false,
                    "AuthorizationType": "NONE",
                    "HttpMethod": "GET",
                    "Integration": {
                        "CacheKeyParameters": [],
                        "Credentials":  { "Fn::GetAtt" : ["APIGWExecRole", "Arn"] },
                        "IntegrationHttpMethod": "POST",
                        "IntegrationResponses": [
                            {
                                "ResponseParameters": {
                                    "method.response.header.Content-Type": "'text/html'"
                                },
                                "ResponseTemplates": {
                                    "text/html": "$input.path('$')"
                                },
                                "StatusCode": "200"
                            }
                        ],
                        "PassthroughBehavior": "WHEN_NO_TEMPLATES",
                        "RequestTemplates": {
                            "application/json": rt_string
                        },
                        "Type": "AWS",
                        "Uri":{ "Fn::Join" : [ "", [ "arn:aws:apigateway:", {"Ref" : "AWS::Region"}, ":lambda:path/2015-03-31/functions/", { "Fn::GetAtt" : [function_name, "Arn"] }, "/invocations" ] ] }
                    },
                    "MethodResponses": [
                        {
                            "ResponseParameters": {
                                "method.response.header.Content-Type": false
                            },
                            "StatusCode": "200"
                        }
                    ],
                    "RequestParameters": RequestParameters,
                    "ResourceId" : resource,
                    "RestApiId" : {"Ref": api_name}
                }
            }
    return method_template

def deployment(api_name, stage_name, method_names, stage_description=None, deployment_description=None):
    deployment_template = {
        "DependsOn": method_names,
        "Type" : "AWS::ApiGateway::Deployment",
        "Properties" : {
            "Description" : "Deployment of the API to alpha.",
            "RestApiId" : {"Ref": api_name},
            "StageDescription" : {
                "CacheClusterEnabled" : false,
                "Description" : "Alpha stage.",
                "MetricsEnabled" : true,
                "StageName" : stage_name
            },
            "StageName" : stage_name
        }
    }
    if stage_description:
        deployment_template["Properties"]["StageDescription"]["Description"] = stage_description
    if deployment_description:
        deployment_template["Properties"]["Description"] = deployment_description
    return deployment_template

def stage(api_name, stage_name, deployment_id, stage_description=None):
    stage_template = {
        "Type" : "AWS::ApiGateway::Stage",
        "Properties" : {
            "CacheClusterEnabled" : false,
            "DeploymentId" : deployment_id,
            "RestApiId" : api_name,
            "StageName" : stage_name
        }
    }
