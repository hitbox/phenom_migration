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
    tenant = appconfig['tenant']
    scope = appconfig['scope']

    signin_url = f'https://signin.ultipro.com/signin/oath2/t/{tenant}/access_token'

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
        'scope': scope,
    }
    response = requests.post(signin_url, data=payload, headers=headers)
    response.raise_for_status()

    print(f'{signin_url} {response.json()=}')

    endpoint_url = (
        f'https://{hostname}/talent/recruiting/v2'
        f'/signin/oauth2/t/{tenant}/access_token'
    )

    print('payload:')
    pprint(payload)

if __name__ == "__main__":
    main()
