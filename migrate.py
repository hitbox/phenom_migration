import argparse
import time
import csv
import json
import logging
import os
import re
import time

from configparser import RawConfigParser
from pprint import pprint
from requests.exceptions import HTTPError

import requests

logger = logging.getLogger('migrate')

class APIClient:

    signin_url = 'https://signin.ultipro.com/signin/oauth2/t/{tenant}/access_token'

    def __init__(
        self,
        hostname,
        tenant,
        client_id,
        client_secret,
        access_token,
        expires_at=None,
    ):
        self.hostname = hostname
        self.tenant = tenant
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.expires_at = expires_at

        self.session = requests.Session()
        self.session.headers.update({
            'accept': 'application/json',
        })
        if access_token:
            self.session.headers['Authorization'] = f'Bearer {access_token}'

    def refresh_access_token(self):
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "accept": "application/json",
        }

        payload = {
            'grant_type': 'client_credentials',
            'client_id': self.client_id,
            'client_secret': self.client_secret,
        }

        url = self.signin_url.format(tenant=self.tenant)
        response = requests.post(url, data=payload, headers=headers)
        response.raise_for_status()

        data = response.json()

        self.access_token = data['access_token']
        expires_in = data.get('expires_in')
        if expires_in:
            self.expires_at = time.time() + expires_in

        self.session.headers['Authorization'] = f'Bearer {self.access_token}'

        logger.info("Refreshed UltiPro access token")

    def get(self, url, **kwargs):
        self.ensure_token()

        for attempt in range(5):
            try:
                response = self.session.get(url, timeout=10, **kwargs)
                if response.status_code == 401:
                    logger.warning("401 received, refreshing token and retrying")
                    self.refresh_access_token()
                    continue  # retry immediately
                response.raise_for_status()
                return response
            except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as e:
                logger.warning(f"Connection error on attempt {attempt+1}/5 for URL: {url}: {e}")
                time.sleep(2 ** attempt)  # exponential backoff
        raise requests.exceptions.ConnectionError(f"Failed to GET {url} after retries")

    def ensure_token(self):
        # Refresh if token is missing or expires within 60 seconds
        if not self.access_token or (
            self.expires_at and time.time() > self.expires_at - 60
        ):
            self.refresh_access_token()

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

        expires_at = None
        if 'expires_in' in data:
            expires_at = time.time() + data['expires_in']

        instance = cls(
            hostname,
            tenant,
            client_id,
            client_secret,
            access_token,
            expires_at = expires_at,
        )
        return instance

    def json_or_raise(self, url):
        return self.get(url).json()

    def try_json(self, url):
        time.sleep(0.2)
        try:
            response = self.get(url)
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.warning('Failed %s', e)
            return None

    def download_file(self, url, dest_path):
        for attempt in range(5):
            try:
                with self.session.get(url, stream=True, timeout=20) as r:
                    r.raise_for_status()
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with open(dest_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                return
            except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as e:
                logger.warning(f"Download failed attempt {attempt+1}/5: {e}")
                time.sleep(2 ** attempt)
        raise requests.exceptions.ConnectionError(f"Failed to download {url} after retries")

    def download(self, url, **kwargs):
        return self.get(url, **kwargs)

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
                response = self.get(url)
                response.raise_for_status()
            except HTTPError:
                break
            logger.info('applications json from url=%s', url)
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

def sanitize_filename(filename: str, max_length: int = 255) -> str:
    # Replace illegal characters with underscore
    sanitized = re.sub(r'[\\/:*?"<>|]', '_', filename)

    # Strip leading/trailing spaces and dots
    sanitized = sanitized.strip(' .')

    # Prevent reserved Windows names
    reserved_names = {
        "CON","PRN","AUX","NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10))
    }
    if sanitized.upper() in reserved_names:
        sanitized = f"_{sanitized}"

    # Truncate to max length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]

    return sanitized
