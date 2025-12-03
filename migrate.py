import argparse
import logging
import requests
from configparser import RawConfigParser

from pprint import pprint
from requests.auth import HTTPBasicAuth
from itertools import permutations

logger = logging.getLogger('migrate')

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level = logging.DEBUG,
    )

    cp = RawConfigParser()
    cp.read(args.config)
    appconfig = cp["migrate"]

    credentials = dict(appconfig)
    keys = credentials.keys()
    values = credentials.values()

    client_id = appconfig['client_id']
    hostname = appconfig['hostname']
    client_secret = appconfig['client_secret']
    tenant_alias = appconfig['tenant_alias']

    endpoint_url = (
        f'https://{hostname}/talent/recruiting/v2'
        f'/signin/oauth2/t/{tenant_alias}/access_token'
    )

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "accept": "application/json",
    }

    payload = {
        'grant_type': 'client_credentials',
        'client_id': client_id,
        'client_secret': client_secret,
        # From email subject:
        # "ATSG - Request for ats name Staging and Production Endpoints"
        'scope': 'recruiting.domain.application-import.read',
    }
    response = requests.post(endpoint_url, data=payload, headers=headers)

    print('payload:')
    pprint(payload)
    print(f'{endpoint_url} {response}')

if __name__ == "__main__":
    main()
