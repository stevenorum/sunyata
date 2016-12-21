#!/usr/bin/env python

import boto3
import datetime
import io
import json
import os
import sys
import zipfile

def zip_file(filename):
    file_like_object = io.BytesIO()
    zipf = zipfile.ZipFile(file_like_object, 'w', zipfile.ZIP_DEFLATED)
    zipf.write(filename)
    zipf.close()
    return io.BytesIO(file_like_object.getvalue())

def zip_directory(dirname):
    file_like_object = io.BytesIO()
    zipf = zipfile.ZipFile(file_like_object, 'w', zipfile.ZIP_DEFLATED)
    for root, dirs, files in os.walk(dirname):
        arcpath = root
        if arcpath.startswith(dirname):
            arcpath = arcpath[len(dirname):]
        if arcpath and arcpath[0] == "/":
            arcpath = arcpath[1:]
        for file in files:
            if file[-1] != "~":
                fname = os.path.join(root, file)
                arcname = os.path.join(arcpath, file)
                zipf.write(fname, arcname)
    zipf.close()
    return io.BytesIO(file_like_object.getvalue())

def zip_function(function):
    if function.get("directory", None):
        return zip_directory(function["directory"])
    else:
        return zip_file(function["file"])

def upload_lambda(function, bucket, key, backup=False):
    s3 = boto3.client("s3")
    fileobj = zip_function(function)
    canonical_key = "{name}-lambda.zip".format(name=function["name"])
    if backup:
        backup_key = "{key}.{suffix}".format(key=key, suffix=datetime.datetime.now().strftime("%Y-%m-%d-%H%M"))
        s3.copy(CopySource={"Bucket":bucket, "Key":key}, Bucket=bucket, Key=backup_key)
    s3.upload_fileobj(Fileobj=fileobj, Bucket=bucket, Key=key)
