import argparse
import logging
import requests
from configparser import RawConfigParser

from pprint import pprint
from requests.auth import HTTPBasicAuth
from itertools import permutations

from zeep import Client
from zeep.transports import Transport

logger = logging.getLogger('migrate')

def get_signin(tenant, client_id, client_secret):
    signin_url = f'https://signin.ultipro.com/signin/oauth2/t/{tenant}/access_token'

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
        #'scope': scope,
    }
    response = requests.post(signin_url, data=payload, headers=headers)
    response.raise_for_status()

    data = response.json()
    return data

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    args = parser.parse_args(argv)

    logging.basicConfig(
        filename = 'output.log',
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
    userkey = appconfig['userkey']
    password = appconfig['password']
    userkey = appconfig['userkey']
    username = appconfig['username']

    signin_data = get_signin(tenant, client_id, client_secret)
    access_token = signin_data['access_token']

    session = requests.Session()
    session.headers.update({'Authorization': f'Bearer {access_token}'})
    transport = Transport(session=session)

    client = Client(
        wsdl='https://service2.ultipro.com/services/BIDataService?wsdl',
        transport=transport,
    )

    result = client.service.LogOn(
        logOnRequest = {
            'UserName': username,
            'Password': password,
        },
    )
    logger.debug(result)

if __name__ == "__main__":
    main()
