import argparse
import calendar
import configparser
import csv
import heapq
import json
import logging.config
import os
import re
import time
import zipfile

from configparser import RawConfigParser
from datetime import datetime
from datetime import timezone
from itertools import count
from operator import itemgetter
from pprint import pprint
from requests.exceptions import HTTPError

import paramiko
import requests

from parse_link import parse_headers_links
from schema import ApplicationSchema
from schema import CandidateSchema
from schema import DocumentSchema
from schema import LinkResultSchema
from schema import LinkSchema

logger = logging.getLogger('migrate')


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
                with self.get(url, stream=True, timeout=20) as response:
                    response.raise_for_status()
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with open(dest_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
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
                if query:
                    response = self.get(url, params=query)
                    query = None
                else:
                    response = self.get(url)
                response.raise_for_status()
            except HTTPError:
                break
            logger.info('applications json from url=%s', url)
            yield response.json()
            # The "next" page url is stuffed in the headers as a weird string
            # under the "link" key.
            link_string = response.headers.get('link', '')

            links = parse_headers_links(link_string)
            for data in links:
                if data['rel'] == 'next':
                    # leak into while loop
                    url = data['href']
                    logger.info('next applications: %s', url)
                    break
            else:
                # "next" key not found.
                logger.info('end of applications: %s', url)
                # None to stop loop
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
                    for document in links.walk_links(application):
                        if is_document_object(document):
                            candidate_data = self.get_candidate(candidate['id'])
                            candidate_context = {
                                'candidate_data': candidate_data,
                                'application': application,
                                'document': document,
                            }
                            yield candidate_context


class FollowLinks:

    link_schema_class = LinkSchema
    link_result_schema_class = LinkResultSchema

    def __init__(
        self,
        client,
        allow_rels,
        link_schema,
        link_result_schema,
    ):
        self.client = client
        self.allow_rels = allow_rels
        self.link_schema = link_schema
        self.link_result_schema = link_result_schema

    def walk_links(self, obj, seen=None):
        """
        Recursively follow UltiPro-style HAL links.
        Yields *all* dict objects encountered,
        including nested ones, but never revisits the same URL twice.
        """
        # Links come from the given object first and then
        # from response headers.
        if seen is None:
            seen = set()

        links = self.link_schema.load(obj['links'], many=True)
        for link_data in links:
            href = link_data['href']
            rel = link_data['rel']

            if self.allow_rels and rel not in self.allow_rels:
                logger.info('Skip rel=%r', rel)
                continue

            # Avoid re-fetching the same link
            if href in seen:
                logger.info('Skipping seen href %s', href)
                continue
            seen.add(href)

            logger.info('Getting json for rel=%r, href=%r', rel, href)
            result_json = self.client.try_json(href)

            if result_json is None:
                logger.info('No data from %r', href)
                continue

            # Pretty sure it's always a list.
            result_list = self.link_result_schema.load(result_json, many=True)
            for item in result_list:
                if isinstance(item, dict):
                    yield item
                    yield from self.walk_links(item, seen)


class UKGRowMapper:

    DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

    MAPPING = {
        'Candidate_ID': lambda self, candidate, application: candidate['id'],
        'Candidate_FirstName': lambda self, candidate, application: candidate['name']['first'],
        'Candidate_LastName': lambda self, candidate, application: candidate['name']['last'],
        'Candidate_EmailID_1_email': lambda self, candidate, application: candidate['contact_info']['email'],
        'Candidate_DateCreated': lambda self, candidate, application: self.format_phenom_datetime(candidate['created_at']),
        'date_updated': lambda self, candidate, application: self.format_phenom_datetime(application['updated_at']),
        'Candidate_Application_ApplicationID': lambda self, candidate, application: application['id'],
        'Candidate_Application_JobID': lambda self, candidate, application: application['opportunity']['id'],
        'Candidate_Application_ApplicationDateCreated': lambda self, candidate, application: self.format_phenom_datetime(application['applied_date']),
        'Candidate_Application_ApplicationDateUpdated': lambda self, candidate, application: self.format_phenom_datetime(application['updated_at']),
        'Candidate_Application_ApplicationStatus': lambda self, candidate, application: application['creation_method'],
        'Application Source': lambda self, candidate, application: safedrill(application, 'applicant_source', 'name', 'en_us'),
        'Candidate_Location_City': lambda self, candidate, application: safedrill(candidate, 'contact_info', 'address', 'city'),
        'Candidate_Location_State': lambda self, candidate, application: safedrill(candidate, 'contact_info', 'address', 'state', 'name', 'en_us'),
        'Candidate_Location_Country': lambda self, candidate, application: safedrill(candidate, 'contact_info', 'address', 'country', 'code'),
        'Candidate_PrimaryPhoneNumber': lambda self, candidate, application: safedrill(candidate, 'contact_info', 'phone', 'primary'),
        #'fileName': lambda self, candidate, application: os.path.basename(download_path),
        'fileName': lambda self, candidate, application: None, # needs to exist for DictWriter fieldnames.
        'Parsable': lambda self, candidate, application: 'Yes', # IDK, they asked for this
    }

    def get_fieldnames(self):
        return list(self.MAPPING.keys())

    def format_date(self, dt):
        if isinstance(dt, datetime):
            return dt.strftime(self.DATE_FORMAT)

    def format_phenom_datetime(self, dt):
        return f"{dt:%Y-%m-%dT%H:%M:%S}.{dt.microsecond // 10000:02d}Z"

    def map_row(self, candidate, application):
        result = {csvkey: func(self, candidate, application)
                  for csvkey, func in self.MAPPING.items()}
        return result
        


class UKGDictWriter(csv.DictWriter):

    def __init__(self, csv_file, **kwargs):
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
            'date_updated': application['updated_at'],
            'Candidate_Application_ApplicationID': application['id'],
            'Candidate_Application_JobID': job_id,
            'Candidate_Application_ApplicationDateCreated': application['applied_date'],
            'Candidate_Application_ApplicationDateUpdated': application['updated_at'],
            'Candidate_Application_ApplicationStatus': application_data['creation_method'],
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


def safedrill(dict_, *keys, abort_none=True, use_last_good=True):
    value = dict_
    for key in keys:
        if abort_none:
            if value is None:
                break
            else:
                # let raise TypeError
                pass
        if key in value:
            value = value[key]
    return value

def removewrap(string, wrapping_chars):
    prefix, suffix = wrapping_chars
    return string.removeprefix(prefix).removesuffix(suffix)

def is_document_object(obj):
    # Has all these keys.
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

def get_sftp_client(options):
    host = options['host']
    port = options['port']
    username = options['username']
    password = options['password']
    transport = paramiko.Transport((host, port))
    transport.connect(username=username, password=password)

    sftp = paramiko.SFTPClient.from_transport(transport)
    return sftp

def replace_time_part(dt, **kwargs):
    """
    Replace the time parts of a datetime, by default with zeros.
    """
    kwargs.setdefault('hour', 0)
    kwargs.setdefault('minute', 0)
    kwargs.setdefault('second', 0)
    kwargs.setdefault('microsecond', 0)
    return dt.replace(**kwargs)

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

    kwargs = {
        'year': year,
        'month': month,
        'day': day,
    }
    return dt.replace(**kwargs)

def ensure_utc(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt

def iso8601_millis(dt):
    """
    Format a datetime as ISO-8601 UTC with milliseconds, e.g.:
    2016-12-21T18:44:03.356Z
    """
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def join_before_extension(filename, string):
    root, ext = os.path.splitext(filename)
    return ''.join([root, string, ext])

def realmain(config, cp, num_months_ago=None):
    now = datetime.now()

    csv_path = cp['scrape']['csv_path'].format(now=now)
    attachment_path = cp['scrape']['attachment_path']
    zip_path = cp['scrape']['zip_path']

    client_config = dict(cp['api_client'])
    client = APIClient.from_signin(**client_config)

    link_follower = FollowLinks(
        client,
        allow_rels = set(['documents']),
        link_schema = LinkSchema(),
        link_result_schema = LinkResultSchema(),
    )

    row_mapper = UKGRowMapper()

    fieldnames = row_mapper.get_fieldnames()

    today_with_time = replace_time_part(now)
    updated_after = months_ago(today_with_time, num_months_ago)
    query = {'updated_after': iso8601_millis(ensure_utc(updated_after))}
    logger.info('query: %s', query)

    application_schema = ApplicationSchema()
    candidate_schema = CandidateSchema()
    document_schema = DocumentSchema()
    # TODO
    # - scrape all the data first
    # - filter/sort json/dicts
    # - only then, start hitting for downloads
    # - maybe only applications are needed for sorting/filtering?

    # Step 1.
    # Scrape the most recent 1,000 applications by updated_at datetime.
    logger.info('Scraping the most recent 1,000 applications since %s', updated_after)

    all_applications = []
    application_pages = client.iter_applications_pages(query=query)
    pagination = enumerate(application_pages, start=1)
    for page, applications_page in pagination:
        for application_number, application_json in enumerate(applications_page, start=1):
            application_data = application_schema.load(application_json)

            all_applications.append(application_data)
        logger.info('page: %s, scraped %s applications', page, len(applications_page))

    logger.info('Done scraping %s applications', len(all_applications))

    # Step 2.
    # Download candidates and files from application objects' data.
    logger.info('Scraping candidates for applications and writing CSV')
    latest_1000_applications = sorted(
        all_applications,
        key = itemgetter('updated_at'),
        reverse = True,
    )
    latest_1000_applications = latest_1000_applications[:1000]
    # Begin writing csv and zip.

    # Set to deduplicate file names.
    filenames = set([])
    with (
        open(csv_path, 'w', newline='', encoding='utf8') as csv_file,
        zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zip_file
    ):
        # with csv and zip file open for writing
        csv_writer = UKGDictWriter(csv_file, fieldnames=fieldnames)
        csv_writer.writeheader()

        for application_data in latest_1000_applications:
            candidate_id = application_data['candidate']['id']
            logger.info('Getting candidate id=%s', candidate_id)
            candidate_json = client.get_candidate(
                candidate_id = candidate_id,
            )
            if candidate_json is None:
                logger.warning('No candidate data for %s', candidate_id)

            candidate_data = candidate_schema.load(candidate_json)

            # Follow links for documents
            for document_json in link_follower.walk_links(application_data):
                document_data = document_schema.load(document_json)

                download_url = client.get_document_download_url(
                    application_data['id'],
                    document_data['id']
                )
                logger.info('download_url=%s', download_url)

                download_path = attachment_path.format(
                    application = application_data,
                    document = document_data,
                )
                download_path = download_path
                logger.info('download_path=%s', download_path)
                filename = sanitize_filename(os.path.basename(download_path))

                if filename in filenames:
                    for index in count():
                        newfn = join_before_extension(filename, f'({index})')
                        if newfn not in filenames:
                            logger.info('file %r unique-ified to %r', filenames, newfn)
                            filename = newfn
                            break
                    else:
                        raise ValueError(f'Unable to make {filename} unique.')

                filenames.add(filename)

                with client.get(download_url, stream=True, timeout=20) as response:
                    response.raise_for_status()
                    with zip_file.open(filename, 'w') as archive_file:
                        for chunk in response.iter_content():
                            if chunk:
                                archive_file.write(chunk)

                csv_row = row_mapper.map_row(candidate_data, application_data)
                csv_row['fileName'] = filename
                csv_writer.writerow(csv_row)

    sftp = get_sftp_client(phenompeople.prod)

    uploads = [
        (zip_path, cp['scrape']['remote_dest_zip'],),
        (csv_path, cp['scrape']['remote_dest_csv'],),
    ]
    for src, dst in uploads:
        sftp.put(src, dst)
        logger.info('sftp.put(%r, %r)', src, dst)

    sftp.close()

    logger.info('done')


def main(argv=None):
    """
    Simple way to investigate the data from the api.
    """
    parser = argparse.ArgumentParser(
        description = main.__doc__
    )
    parser.add_argument('config', nargs='+')
    parser.add_argument('--months-ago', type=int, default='8')

    args = parser.parse_args(argv)

    cp = configparser.ConfigParser()
    cp.read(args.config)

    if set(['loggers', 'handlers', 'formatters']).issubset(cp):
        # logging available from config file, use it.
        logging.config.fileConfig(cp)

    realmain(args.config, cp, args.months_ago)

if __name__ == '__main__':
    main()
