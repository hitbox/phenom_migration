import argparse
import calendar
import csv
import json
import logging
import os
import re
import time

from configparser import RawConfigParser
from datetime import datetime
from datetime import timezone
from pprint import pprint
from requests.exceptions import HTTPError

import paramiko
import requests

logger = logging.getLogger(__name__)

class DownloadFailed(Exception):
    pass

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
        for attempt in range(10):
            response = requests.post(url, data=payload, headers=headers)
            try:
                response.raise_for_status()
            except HTTPError as e:
                if response.status_code == 503:
                    sleep_time = 60 * attempt
                    logger.warning('Login service unavailable, sleeping %s seconds', sleep_time)
                    # Exponential backup for host down
                    time.sleep(sleep_time)
                else:
                    raise
            else:
                # Success
                break

        data = response.json()

        self.access_token = data['access_token']
        expires_in = data.get('expires_in')
        if expires_in:
            self.expires_at = time.time() + expires_in

        self.session.headers['Authorization'] = f'Bearer {self.access_token}'

        logger.info("Refreshed UltiPro access token")

    def get(self, url, retries=5, **kwargs):
        self.ensure_token()
        kwargs.setdefault('timeout', 10)

        for attempt in range(retries):
            try:
                response = self.session.get(url, **kwargs)
                if response.status_code == 401:
                    logger.warning("401 received, refreshing token and retrying")
                    self.refresh_access_token()
                    continue  # retry immediately
                maintenance_msg = 'The page you are trying to access is temporarily unavailable for maintenance.'
                if maintenance_msg in response.text:
                    # TODO
                    # - We're dependent on this being the first thing to call to the api expecting json.
                    # - Probably move this to anywhere we expect json.
                    # Sleep for maintenance and try again with same url.
                    sleep_time = 60 * 60
                    logger.warning('Sleeping %s seconds for maintenance mode', sleep_time)
                    time.sleep(sleep_time)
                    continue
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                logger.warning(f"Connection error on attempt {attempt+1}/5 for URL: {url}: {e}")
                time.sleep(2 ** attempt)  # exponential backoff
        raise DownloadFailed(f"Failed to GET {url} after {retries} retries")

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

    def try_json(self, url, retries=3):
        time.sleep(0.2)
        for attempt in range(retries):
            try:
                response = self.get(url)
                data = safe_json_decode(response)
                if data is not None:
                    return data
                logger.warning(
                    'Failed json decode from %s, attempt %s/%s', url, attempt+1, retries)
                time.sleep(2 ** attempt)
            except requests.exceptions.RequestException as e:
                logger.warning('Failed %s, attempt %s/%s', e, attempt+1, retries)
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        logger.error('All retries exhausted for %s', url)
        return None

    def download_file(self, url, dest_path, retries=10):
        for attempt in range(retries):
            try:
                with self.get(url, stream=True, timeout=20) as r:
                    r.raise_for_status()
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with open(dest_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
                return f'success after {attempt} attempts'
            except requests.exceptions.RequestException as e:
                logger.warning(f"Download failed attempt {attempt+1}/{retries}: {e}")
                time.sleep(2 ** attempt)
        raise DownloadFailed(f"Failed to download {url} after retries")

    def download(self, url, **kwargs):
        return self.get(url, **kwargs)

    def iter_applications_pages(self, query=None):
        """
        Iterate the pages of applications data.
        """
        if query is None:
            query = {}
        url = (
            f'https://{self.hostname}/talent/recruiting/v2/'
            f'{self.tenant}/api/applications'
        )
        while url:
            # The link data seems to give us a next-url that we just check for
            # failure.
            try:
                response = self.get(url, params=query)
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
                # Only send query for first request to avoid appending the params again and again.
                query = None
                logger.info('next applications: %s', url)
            else:
                logger.info('end of applications: %s', url)
                url = None

    def get_application(self, application_id):
        url = f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}/api/applications/{application_id}'
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

    def iter_applications_and_candidates(self, processed_applications=None, query=None):
        if processed_applications is None:
            processed_applications = set()

        for applications in self.iter_applications_pages(query=query):
            for application in applications:
                application_id = application['id']


                if application_id in processed_applications:
                    logger.info(
                        'Skipping already processed application %s', application_id)
                    continue

                processed_applications.add(application_id)

                candidates = application.get('candidate', {})
                if not isinstance(candidates, list):
                    candidates = [candidates]

                for candidate in candidates:
                    links = FollowLinks(self, application)
                    for document in links.follow_links(application):
                        if is_document_object(document):
                            candidate_data = self.get_candidate(candidate['id'])
                            candidate_context = {
                                'candidate_data': candidate_data,
                                'application': application,
                                'document': document,
                            }
                            yield candidate_context


class FollowLinks:

    def __init__(self, client, application):
        self.client = client
        self.application = application # data

    def follow_links(self, obj, seen=None, rels=None):
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
            rel = link.get('rel')
            if not href:
                logging.info('Skipping empty href')
                continue

            if rels and rel not in rels:
                logging.info('Skip rel=%r not in %r', rel, rels)
                continue

            # Avoid re-fetching the same link
            if href in seen:
                logging.info('Skipping seen href %s', href)
                continue
            seen.add(href)

            logger.info('Getting json for rel=%r, href=%r', rel, href)
            data = self.client.try_json(href)
            if data is None:
                continue

            # If the API returns a list, recurse into every element
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        yield item
                        yield from self.follow_links(item, seen, rels=rels)

            # If it’s a single dict, yield it and recurse
            elif isinstance(data, dict):
                yield data
                yield from self.follow_links(data, seen, rels=rels)


class UKGDictWriter(csv.DictWriter):

    fieldnames = [
        'Candidate_ID',
        'Candidate_FirstName',
        'Candidate_LastName',
        'Candidate_EmailID_1_email',
        'Candidate_DateCreated',
        'date_updated',
        'Candidate_Application_ApplicationID',
        'Candidate_Application_JobID',
        'Candidate_Application_ApplicationDateCreated',
        'Candidate_Application_ApplicationDateUpdated',
        'Candidate_Application_ApplicationStatus',
        'Application Source',
        'Candidate_Location_City',
        'Candidate_Location_State',
        'Candidate_Location_Country',
        'Candidate_PrimaryPhoneNumber',
        'fileName',
        'Parsable',
        'download_status',
    ]

    def __init__(self, csv_file, **kwargs):
        kwargs.setdefault('fieldnames', self.fieldnames)
        super().__init__(csv_file, **kwargs)
        self.csv_file = csv_file

    def write_ukg_row(
        self,
        candidate,
        candidate_data,
        application,
        application_data,
        download_path,
        download_status,
    ):
        email = candidate_data['contact_info'].get('email')
        candidate_name = candidate.get('name', {})
        job_id = application_data['opportunity']['id']
        applicant_source = application_data.get('applicant_source')
        application_source = applicant_source.get('name', {}).get('en_us')
        contact_info = candidate_data.get('contact_info', {})
        if contact_info is not None:
            address = contact_info.get('address', {})
            if address is not None:
                city = address.get('city')
                country = address.get('country', {}).get('code')
                state = address.get('state').get('code')
            else:
                city = None
                country = None
                state = None
            phone = contact_info.get('phone', {})
            if phone:
                phone = phone.get('primary')
        else:
            phone = None
        row_data = {
            'Candidate_ID': candidate.get('id'),
            'Candidate_FirstName': candidate_name.get('first'),
            'Candidate_LastName': candidate_name.get('last'),
            'Candidate_EmailID_1_email': email,
            'Candidate_DateCreated': candidate_data['created_at'],
            'date_updated': application.get('updated_at'),
            'Candidate_Application_ApplicationID': application['id'],
            'Candidate_Application_JobID': job_id,
            'Candidate_Application_ApplicationDateCreated': application.get('applied_date'),
            'Candidate_Application_ApplicationDateUpdated': application.get('updated_at'),
            'Candidate_Application_ApplicationStatus': application_data.get('creation_method'),
            'Application Source': application_source,
            'Candidate_Location_City': city,
            'Candidate_Location_State': state,
            'Candidate_Location_Country': country,
            'Candidate_PrimaryPhoneNumber': phone,
            'fileName': os.path.basename(download_path),
            'Parsable': 'Yes', # IDK, they asked for this
        }
        row_data['download_status'] = download_status
        super().writerow(row_data)


def is_document_object(obj):
    return set(['document_type', 'file_name', 'id']).issubset(obj)

def sftp_makedirs(sftp, remote_path, exist_ok=False):
    """
    Recursively create directories on SFTP server.
    """
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
    # Replace whitespace with underscores
    filename = re.sub(r'\s+', '_', filename)

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

def safe_json_decode(response):
    """
    Safely decode JSON with fallback error handling.
    """
    try:
        response.encoding = 'utf-8'
        return response.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error for URL {response.url}")
        logger.error(f"Response status: {response.status_code}")
        logger.error(f"Response text (first 500 chars): {response.text[:500]}")
        logger.error(f"Decode error: {e}")
        return None

def scrape_ukg_api(client, writer, attachment_path, checkpoint_path=None, skip_existing=False, query=None):
    """
    Scrape UKG API, download documents, and write to CSV.
    """
    processed_applications = set()

    # Load checkpoint data if exists.
    if checkpoint_path and os.path.exists(checkpoint_path):
        with open(checkpoint_path, 'r') as checkpoint_file:
            checkpoint = json.load(checkpoint_file)
            processed_applications = set(checkpoint.get('processed', []))
        logger.info('Resumed: %s applications already processed', len(processed_applications))

    checkpoint_counter = 0
    rows_written = 0
    for applications in client.iter_applications_pages(query=query):
        for application in applications:
            application_id = application['id']
            try:
                application_data = client.get_application(application_id)
            except:
                logger.exception('An error occurred while getting application')

            if application_id in processed_applications:
                logger.info('Skipping already processed application %s', application_id)
                continue

            try:
                candidates = application.get('candidate', {})
                if not isinstance(candidates, list):
                    candidates = [candidates]

                rels = set(['documents'])
                for candidate in candidates:
                    # Follow links for documents
                    links = FollowLinks(client, application)
                    for document in links.follow_links(application, rels=rels):
                        if is_document_object(document):
                            filename = sanitize_filename(document['file_name'])
                            # Add local path to filename from format string.
                            download_path = attachment_path.format(
                                application=application,
                                filename=filename
                            )

                            download_status = None
                            try:
                                # Download file if it doesn't exist
                                if skip_existing and os.path.exists(download_path):
                                    logger.info('File already exists: %s', download_path)
                                else:
                                    # Try to download file as is by given
                                    # filename. Sanitize filename if it throws
                                    # an error. Open written file and read a
                                    # byte; and stat file to confirm it will be
                                    # readable again.
                                    download_url = client.get_document_download_url(
                                        application_id,
                                        document['id']
                                    )
                                    os.makedirs(os.path.dirname(download_path), exist_ok=True)
                                    download_status = None
                                    try:
                                        client.download_file(download_url, download_path)
                                    except OSError:
                                        download_path = sanitize_filename(download_path)
                                        client.download_file(download_url, download_path)
                                        download_status = 'success after sanitize'
                                    try:
                                        with open(download_path, 'rb') as test_file:
                                            # test reading a byte to verify the file can be read back
                                            test_file.read(1)
                                        os.stat(download_path)
                                    except FileNotFoundError as e:
                                        download_status = str(e)
                                    logger.info('Downloaded: %s', download_path)
                            except DownloadFailed as e:
                                download_status = str(e)
                            finally:
                                # Always write CSV row whether file was skipped for existing or not.
                                candidate_data = client.get_candidate(candidate['id'])
                                writer.write_ukg_row(
                                    candidate,
                                    candidate_data,
                                    application,
                                    application_data,
                                    download_path,
                                    download_status,
                                )
                                rows_written += 1
                                if rows_written % 100 == 0:
                                    writer.csv_file.flush()

                # Mark application as processed
                processed_applications.add(application_id)
                checkpoint_counter += 1

                # Save checkpoint every 10 applications
                if checkpoint_path and checkpoint_counter % 10 == 0:
                    with open(checkpoint_path, 'w') as f:
                        json.dump({'processed': list(processed_applications)}, f)
                    logger.info(
                        'Checkpoint saved: %s applications processed',
                        len(processed_applications)
                    )

            except Exception as e:
                logger.error(
                    "Error processing application: %s",
                    application_id,
                    exc_info=True
                )
                # Save checkpoint before potentially crashing
                if checkpoint_path:
                    with open(checkpoint_path, 'w') as f:
                        json.dump({'processed': list(processed_applications)}, f)
                raise

    # Final checkpoint save
    if checkpoint_path:
        with open(checkpoint_path, 'w') as f:
            json.dump({'processed': list(processed_applications)}, f)
        logger.info(
            'Final checkpoint saved: %s applications processed',
            len(processed_applications)
        )
    # Return True for complete run because we're eating the exceptions.
    return True

def get_sftp(options):
    host = options['host']
    port = options['port']
    username = options['username']
    password = options['password']
    transport = paramiko.Transport((host, port))
    transport.connect(username=username, password=password)

    sftp = paramiko.SFTPClient.from_transport(transport)
    return sftp

def months_ago(dt, months):
    year = dt.year
    month = dt.month - months

    # adjust year/month underflow
    while month <= 0:
        month += 12
        year -= 1

    # clamp day to last valid day of target month
    last_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, last_day)

    return dt.replace(year=year, month=month, day=day)

def iso8601_millis(dt):
    """
    Format a datetime as ISO-8601 UTC with milliseconds, e.g.:
    2016-12-21T18:44:03.356Z
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
