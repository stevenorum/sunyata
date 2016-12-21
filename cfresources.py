#!/usr/bin/env python

DEFAULT_NAME = "sunyata"

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
