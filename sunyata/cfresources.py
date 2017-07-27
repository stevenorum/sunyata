#!/usr/bin/env python3

import json
from sunyata.canonicalize import *

DEFAULT_NAME = "sunyata"
false = False
true = True
null = None

def api_domain_mapping(domain, api_name, base_path="", stage="alpha", depends_on=None):
    mapping_template = {
        "Type" : "AWS::ApiGateway::BasePathMapping",
        "Properties" : {
            "BasePath" : base_path,
            "DomainName" : domain,
            "RestApiId" : {"Ref":api_name},
            "Stage" : stage
            }
        }
    if depends_on:
        mapping_template["DependsOn"] = depends_on
    return mapping_template

def public_bucket_bundle(logical_name, bucket_name=None):
    return {
        canonical_bucket_name(logical_name):bucket(name=bucket_name, website=True),
        canonical_bucket_policy_name(logical_name):public_bucket_policy(canonical_bucket_name(logical_name))
        }

def bucket(name=None, website=False, cors=False):
    bucket_template = {
        "Type" : "AWS::S3::Bucket",
        "Properties" : {}
    }
    if name:
        bucket_template["Properties"]["BucketName"] = name
    if website:
        bucket_template["Properties"]["WebsiteConfiguration"] = {
            "IndexDocument" : "index.html"
            }
    if cors:
        bucket_template["Properties"]["CorsConfiguration"] = {
            "CorsRules" : [
                {
                    "AllowedHeaders" : [ "*" ],
                    "AllowedMethods" : [ "GET" ],
                    "AllowedOrigins" : [ "https://*" ],
                    "ExposedHeaders" : [ "x-amz-server-side-encryption","x-amz-request-id","x-amz-id-2" ],
                    "MaxAge" : 3000
                    }
                ]
            }
    return bucket_template

def public_bucket_policy(target, name=None):
    policy_template = {
        "Type" : "AWS::S3::BucketPolicy",
        "Properties" : {
            "Bucket" : {"Ref": target},
            "PolicyDocument" : {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "AddPerm",
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": "s3:GetObject",
                        "Resource": {"Fn::Join": [ "", ["arn:aws:s3:::",{"Ref": target},"/*"]]}
                        }
                    ]
                }
            }
        }
    return policy_template

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
            "FunctionName" : prefixAPI(name),
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

def method(function_name, resource, api_name, content_type="text/html", querystring_params={}, extra={}, integration_type=None, http_method="GET", enable_cors=False, model={}, redirect=None):
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
                    "HttpMethod": http_method,
                    "Integration": {
                        "CacheKeyParameters": [],
                        "Credentials":  { "Fn::GetAtt" : ["APIGWExecRole", "Arn"] },
                        "IntegrationHttpMethod": "POST",
                        "IntegrationResponses": [
                            {
                                "ResponseParameters": {
                                    "method.response.header.Content-Type": "'" + content_type + "'"
                                },
                                "ResponseTemplates": {
                                    content_type: "$input.path('$')"
                                },
                                "StatusCode": "200"
                            }
                        ],
                        "PassthroughBehavior": "WHEN_NO_TEMPLATES",
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
#     if RequestTemplate:
    method_template["Properties"]["Integration"]["RequestTemplates"] = {"application/json":rt_string}
    if enable_cors:
        method_template["Properties"]["Integration"]["IntegrationResponses"][0]["ResponseParameters"]["method.response.header.Access-Control-Allow-Origin"] = "'*'"
        method_template["Properties"]["MethodResponses"][0]["ResponseParameters"]["method.response.header.Access-Control-Allow-Origin"] = "'*'"
    if integration_type and integration_type != "AWS" and integration_type in ["AWS","HTTP","AWS_PROXY","HTTP_PROXY","MOCK"]:
        method_template["Properties"]["Integration"]["Type"] = integration_type
        if integration_type == "AWS_PROXY":
            del method_template["Properties"]["Integration"]["IntegrationResponses"]
    if model:
        method_template["Properties"]["RequestModels"] = {}
        method_template["Properties"]["RequestModels"]["application/json"] = model
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

def model(api_name, model_name, model):
    model_template = {
        "Type" : "AWS::ApiGateway::Model",
        "Properties" : {
            "ContentType" : "application/json",
            "Name" : model_name,
            "RestApiId" : { "Ref": api_name},
            "Schema" : model
            }
        }
    model_template["Properties"]["Schema"]["$schema"] = "http://json-schema.org/draft-04/schema#"
    model_template["Properties"]["Schema"]["title"] = model_name
    return model_template

def cors_enabling_method(resource, api_name):
    return {
        "Type": "AWS::ApiGateway::Method",
            "Properties": {
            "AuthorizationType": "NONE",
            "RestApiId": {
                "Ref": api_name
                },
            "ResourceId": resource,           
            "HttpMethod": "OPTIONS",
            "Integration": {
                "IntegrationResponses": [
                    {
                        "StatusCode": 200,
                        "ResponseParameters": {
                            "method.response.header.Access-Control-Allow-Headers": "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'",
                            "method.response.header.Access-Control-Allow-Methods": "'GET,POST,OPTIONS'",
                            "method.response.header.Access-Control-Allow-Origin": "'*'"
                            },
                        "ResponseTemplates": {
                            "application/json": ""
                            }
                        }
                    ],
                "PassthroughBehavior": "WHEN_NO_MATCH",
                "RequestTemplates": {
                    "application/json": "{\"statusCode\": 200}"
                    },
                "Type": "MOCK"
                },
            "MethodResponses": [
                {
                    "StatusCode": 200,
                    "ResponseModels": {
                        "application/json": "Empty"
                        },
                    "ResponseParameters": {
                        "method.response.header.Access-Control-Allow-Headers": false,
                        "method.response.header.Access-Control-Allow-Methods": false,
                        "method.response.header.Access-Control-Allow-Origin": false
                        }
                    }
                ]
            }
        }

#def cloudwatch_cron()

#     method_template = {
#         "Type" : "AWS::ApiGateway::Method",
#         "Properties" : {
#             "ApiKeyRequired": false,
#             "AuthorizationType": "NONE",
#             "HttpMethod": "OPTIONS",
#             "Integration": {
#                 "IntegrationHttpMethod": "POST",
#                 "IntegrationResponses": [
#                     {
#                         "ResponseParameters": {
#                             "method.response.header.Access-Control-Allow-Headers": "'Content-Type,X-Amz-Date,Authorization,X-Api-Key,X-Amz-Security-Token'",
#                             "method.response.header.Access-Control-Allow-Methods": "'POST,OPTIONS'",
#                             "method.response.header.Access-Control-Allow-Origin": "'*'"
#                             },
#                         "ResponseTemplates": {
#                             "application/json": "$input.path('$')"
#                             },
#                         "StatusCode": "200"
#                         }
#                     ],
#                 "PassthroughBehavior": "WHEN_NO_MATCH",
#                 "RequestTemplates": {
#                     "application/json": "{\"statusCode\": 200}"
#                     },
#                 "Type": "MOCK"
#                 },
#             "MethodResponses": [
#                 {
#                     "ResponseModels": {
#                         "application/json": "Empty"
#                         },
#                     "ResponseParameters": {
#                         "method.response.header.Access-Control-Allow-Headers": false,
#                         "method.response.header.Access-Control-Allow-Methods": false,
#                         "method.response.header.Access-Control-Allow-Origin": false
#                         },
#                     "StatusCode": "200"
#                     }
#                 ],
#             "ResourceId" : resource,
#             "RestApiId" : {"Ref": api_name}
#             }
#         }
#     return method_template
