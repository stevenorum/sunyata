#!/usr/bin/env python3

import boto3
import datetime
import hashlib
import io
import json
import logging
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

S3 = None

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
        zipf.getinfo(config_path).external_attr = 0o777 << 16
    zipf.close()
    return io.BytesIO(file_like_object.getvalue())

def zip_function(function, config_path=None, config=None):
    if function.get("directory", None):
        return zip_directory(function["directory"], config_path=config_path, config=config)
    else:
        return zip_file(function["file"])

def upload_lambda(function, bucket, key, config_path=None, config=None):
    fileobj = zip_function(function, config_path=config_path, config=config)
    full_key = "{key}.{suffix}".format(key=key, suffix=datetime.datetime.now().strftime("%Y-%m-%d-%H%M"))
    upload_body(bucket=bucket, key=full_key, body=fileobj.read())
    S3.copy(CopySource={"Bucket":bucket, "Key":full_key}, Bucket=bucket, Key=key)
    return full_key

def upload_static(bucket, directory):
    files_uploaded = []
    for root, dirs, files in os.walk(directory):
        for filename in files:
            path_on_disk = os.path.join(root, filename)
            path_in_bucket = strip_prepath(path_on_disk, directory)
            logging.debug("Uploading static file {fname}".format(fname=path_on_disk))
            with open(path_on_disk, "rb") as f:
                body = f.read()
            if not upload_body(bucket=bucket, key=path_in_bucket, body=body):
                logging.debug("File at {path_on_disk} already uploaded to {bucket}/{key}".format(path_on_disk=path_on_disk, bucket=bucket, key=path_in_bucket))
            files_uploaded.append(path_in_bucket)
    return files_uploaded

def upload_body(bucket, key, body):
    global S3
    S3 = S3 if S3 else boto3.client("s3")
    md5=hashlib.md5(body).hexdigest()
    # I don't really care about this, but S3 requires a metadata change if an object is copied to itself, so including this guarantees that.
    utime=datetime.datetime.now().strftime("%Y-%m-%d-%H%M")
    ct = get_content_type(key, body)
    cc = "max-age=60;s-maxage=3600"
    try:
        existing = S3.get_object(Bucket=bucket, Key=key, Range="bytes=0-0")
        existing_md5 = existing.get("Metadata", {}).get("sunyata-md5", None)
        existing_ct = existing["ContentType"]
        existing_etag = existing["ETag"]
        existing_cc = existing.get("CacheControl", None)
        if md5 == existing_md5 or md5 == existing_etag:
            # The correct bytes are already in the correct place
            if ct != existing_ct or md5 != existing_md5 or cc != existing_cc:
                # They're tagged with the wrong content-type.  Fix that or stuff doesn't work.
                # Or maybe they're just missing the MD5 tag.  Go ahead and add that as the etag check isn't guaranteed to work.
                S3.copy_object(Bucket=bucket, Key=key, Metadata={"sunyata-md5":md5,"utime":utime}, ContentType=ct, CopySource={"Bucket":bucket,"Key":key}, CacheControl=cc)
            return False
    except Exception as e:
        logging.exception("Error while checking if file already uploaded: " + str(e))
    logging.debug("Uploading file to {bucket}/{key}".format(bucket=bucket, key=key))
    S3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=ct, Metadata={"sunyata-md5":md5,"utime":utime}, CacheControl=cc)
    return True
