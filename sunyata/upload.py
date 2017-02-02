#!/usr/bin/env python

import boto3
import datetime
import io
import json
import os
import sys
import zipfile

content_types = {
"jpg":"image/jpg",
"jpeg":"image/jpeg",
"png":"image/png",
"gif":"image/gif",
"bmp":"image/bmp",
"tiff":"image/tiff",
"txt":"text/plain",
"rtf":"application/rtf",
"ttf":"font/ttf",
"css":"text/css",
"html":"text/html",
"js":"application/javascript",
"eot":"application/vnd.ms-fontobject",
"svg":"image/svg+xml",
"woff":"application/x-font-woff",
"woff2":"application/x-font-woff",
"otf":"application/x-font-otf",
"json":"application/json",
}

def get_content_type(fname, body):
    return content_types.get(fname.split(".")[-1].lower(),"binary/octet-stream")

def zip_file(filename):
    file_like_object = io.BytesIO()
    zipf = zipfile.ZipFile(file_like_object, 'w', zipfile.ZIP_DEFLATED)
    zipf.write(filename)
    zipf.close()
    return io.BytesIO(file_like_object.getvalue())

def strip_prepath(path, prepath):
    if path.startswith(prepath):
        path = path[len(prepath):]
    if path and path[0] == "/":
        path = path[1:]
    return path

def zip_directory(dirname, config_path=None, config=None):
    file_like_object = io.BytesIO()
    zipf = zipfile.ZipFile(file_like_object, 'w', zipfile.ZIP_DEFLATED)
    for root, dirs, files in os.walk(dirname):
        arcpath = strip_prepath(root, dirname)
        for file in files:
            if file[-1] != "~":
                fname = os.path.join(root, file)
                arcname = os.path.join(arcpath, file)
                zipf.write(fname, arcname)
    if config_path:
        if config_path in zipf.namelist():
            raise RuntimeError("Requested config path {config_path} conflicts with a user-provided source file.".format(config_path=config_path))
        # Dump the config in as compressed a form as possible.
        zipf.writestr(config_path, json.dumps(config, separators=(',',':')))
        zipf.getinfo(config_path).external_attr = 0777 << 16L
    zipf.close()
    return io.BytesIO(file_like_object.getvalue())

def zip_function(function, config_path=None, config=None):
    if function.get("directory", None):
        return zip_directory(function["directory"], config_path=config_path, config=config)
    else:
        return zip_file(function["file"])

def upload_lambda(function, bucket, key, config_path=None, config=None):
    s3 = boto3.client("s3")
    fileobj = zip_function(function, config_path=config_path, config=config)
    canonical_key = "{name}-lambda.zip".format(name=function["name"])
    full_key = "{key}.{suffix}".format(key=key, suffix=datetime.datetime.now().strftime("%Y-%m-%d-%H%M"))
    s3.upload_fileobj(Fileobj=fileobj, Bucket=bucket, Key=full_key)
    s3.copy(CopySource={"Bucket":bucket, "Key":full_key}, Bucket=bucket, Key=key)
    return full_key

def upload_static(bucket, directory):
    files_uploaded = []
    s3 = boto3.client("s3")
    for root, dirs, files in os.walk(directory):
        for filename in files:
            path_on_disk = os.path.join(root, filename)
            path_in_bucket = strip_prepath(path_on_disk, directory)
            with open(path_on_disk, "r") as f:
                body = f.read()
            s3.put_object(Bucket=bucket, Key=path_in_bucket, Body=body, ContentType=get_content_type(path_in_bucket, body))
            files_uploaded.append(path_in_bucket)
    return files_uploaded
