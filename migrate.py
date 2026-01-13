import argparse
import csv
import json
import logging
import os
import re
import requests

from configparser import RawConfigParser
from pprint import pprint
from requests.exceptions import HTTPError

logger = logging.getLogger('migrate')

download_re = re.compile(
    r'http://service2.ultipro.com/AIR1013ATSG/api/applications/'
    f'(?P<id1>[a-z0-9-]+)/documents/(?P<id2>[a-z0-9-]+)/download'
)

class APIClient:

    signin_url = 'https://signin.ultipro.com/signin/oauth2/t/{tenant}/access_token'

    def __init__(
        self,
        hostname,
        tenant,
        client_id,
        client_secret,
        access_token,
    ):
        self.hostname = hostname
        self.tenant = tenant
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token

    @property
    def headers(self):
        return {
            'accept': 'application/json',
            'Authorization': f'Bearer {self.access_token}'
        }

    @classmethod
    def from_signin(cls, hostname, tenant, client_id, client_secret):
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "accept": "application/json",
        }

        # From email subject:
        # "ATSG - Request for ats name Staging and Production Endpoints"
        payload = {
            'grant_type': 'client_credentials',
            'client_id': client_id,
            'client_secret': client_secret,
        }
        url = cls.signin_url.format(**locals())
        response = requests.post(url, data=payload, headers=headers)
        response.raise_for_status()

        data = response.json()
        access_token = data['access_token']

        instance = cls(
            hostname,
            tenant,
            client_id,
            client_secret,
            access_token,
        )
        return instance

    def json_or_raise(self, url):
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def try_json(self, url):
        response = requests.get(url, headers=self.headers)
        if response.ok:
            return response.json()

    def document_download_url(self, application_id, document_id):
        download_url = (
            f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}'
            f'/api/applications/{application_id}/documents/'
            f'{document_id}/download'
        )
        return download_url

    def download(self, url, **kwargs):
        headers = {
            'Authorization': f'Bearer {self.access_token}'
        }
        return requests.get(url, **kwargs)

    def iter_applications_pages(self):
        """
        Iterate the pages of applications data.
        """
        url = (
            f'https://{self.hostname}/talent/recruiting/v2/'
            f'{self.tenant}/api/applications'
        )
        while url:
            # The link data seems to give us a next-url that we just check for
            # failure.
            try:
                response = requests.get(url, headers=self.headers)
                response.raise_for_status()
            except HTTPError:
                break
            yield response.json()
            # The "next" page url is stuffed in the headers as a weird string
            # under the "link" key.
            link = response.headers.get('link', '')
            # Search for the url inside <> for the next url page.
            match = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if match:
                url = match.group(1)
                logger.info('next applications: %s', url)
            else:
                logger.info('end of applications: %s', url)
                url = None

    def get_applicants(self):
        url = f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}/api/applications'
        return self.json_or_raise(url)

    def get_candidate(self, candidate_id):
        url = (
            f'https://{self.hostname}/talent/recruiting/v2'
            f'/{self.tenant}/api/candidates/{candidate_id}'
        )
        return self.json_or_raise(url)

    def get_candidates(self):
        url = f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}/api/candidates'
        return self.json_or_raise(url)

    def get_applications(self):
        url = f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}/api/applications'
        return self.json_or_raise(url)

    def get_applications_for_candidate(self, candidate_id):
        url = (
            f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}'
            f'/api/applications/candidate/{candidate_id}'
        )
        return self.json_or_raise(url)

    def get_document_download_url(self, application_id, document_id):
        url = (
            f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}'
            f'/api/applications/{application_id}/documents/'
            f'{document_id}/download'
        )
        return url


def get_signin(tenant, client_id, client_secret, username, password):
    signin_url = f'https://signin.ultipro.com/signin/oauth2/t/{tenant}/access_token'

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "accept": "application/json",
    }

    # From email subject:
    # "ATSG - Request for ats name Staging and Production Endpoints"
    payload = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
    }
    response = requests.post(signin_url, data=payload, headers=headers)
    response.raise_for_status()

    data = response.json()
    return data

def deep_get(dict_, *keys):
    for key in keys:
        dict_ = dict_.get(key)
    return dict_

filename_keys = set([
    'document_type',
    'file_name',
])

class FollowLinks:

    def __init__(self, client, application):
        self.client = client
        self.application = application # data

    def follow_links(self, obj, seen=None):
        """
        Recursively follow UltiPro-style HAL links.
        Yields *all* dict objects encountered,
        including nested ones, but never revisits the same URL twice.
        """
        if seen is None:
            seen = set()

        links = obj.get('links', [])
        for link in links:
            href = link.get('href')
            if not href:
                continue

            # Avoid re-fetching the same link
            if href in seen:
                continue
            seen.add(href)

            data = self.client.try_json(href)
            if data is None:
                continue

            # If the API returns a list, recurse into every element
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        yield item
                        yield from self.follow_links(item, seen)

            # If it’s a single dict, yield it and recurse
            elif isinstance(data, dict):
                yield data
                yield from self.follow_links(data, seen)


def is_document_object(obj):
    return set(['document_type', 'file_name', 'id']).issubset(obj)

def sftp_makedirs(sftp, remote_path, exist_ok=False):
    """Recursively create directories on SFTP server."""
    dirs = remote_path.strip('/').split('/')
    path = ''
    for dir_part in dirs:
        path += f'/{dir_part}'
        try:
            sftp.mkdir(path)
        except IOError:
            # Directory probably exists, ignore
            if not exist_ok:
                raise

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('config')
    args = parser.parse_args(argv)

    cp = RawConfigParser()
    cp.read(args.config)
    appconfig = cp['migrate']

    client_id = appconfig['client_id']
    apps_dir = appconfig['apps_dir']
    hostname = appconfig['hostname']
    client_secret = appconfig['client_secret']
    tenant = appconfig['tenant']
    userkey = appconfig['userkey']
    password = appconfig['password']
    username = appconfig['username']
    csv_output = appconfig['csv_output']
    attachments_dir = appconfig['attachments_dir']
    attachment_path = appconfig['attachment_path']

    signin_data = get_signin(tenant, client_id, client_secret, username, password)
    access_token = signin_data['access_token']

    client = APIClient(
        hostname,
        tenant,
        client_id,
        client_secret,
        access_token,
        username,
        password,
    )
if __name__ == "__main__":
    main()
