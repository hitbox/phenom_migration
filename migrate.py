import argparse
import csv
import json
import logging
import os
import requests.exceptions

from configparser import RawConfigParser
from pprint import pprint

from marshmallow import Schema
from marshmallow import fields
from marshmallow.fields import Boolean
from marshmallow.fields import Date
from marshmallow.fields import DateTime
from marshmallow.fields import Integer
from marshmallow.fields import List
from marshmallow.fields import Nested
from marshmallow.fields import Dict
from marshmallow.fields import String

logger = logging.getLogger('migrate')

class LinkSchema(Schema):

    href = String()
    rel = String()


class CreatorSchema(Schema):

    id = String()


class RecruitingProcessSchema(Schema):

    id = String()
    step_id = String()


class NameSchema(Schema):

    first = String()
    last = String()
    former = String(allow_none=True)
    middle = String(allow_none=True)
    prefix = Dict(allow_none=True)
    suffix = Dict(allow_none=True)

class CandidateSchema(Schema):

    id = String()
    is_internal = Boolean()
    is_active = Boolean()
    employment_status = String(allow_none=True)
    name = Nested(NameSchema)


class FromSchema(Schema):

    month = Integer(allow_none=True)
    year = Integer(allow_none=True)

class WorkExperienceSchema(Schema):

    id = String()
    links = List(Nested(LinkSchema))
    job_title = String()
    company = String()
    location = String(allow_none=True)
    from_ = Nested(FromSchema, data_key='from', allow_none=True)
    to = Nested(FromSchema, allow_none=True)
    description = String(allow_none=True)


class JobPostingSchema(Schema):

    id = String()


class SkillSchema(Schema):

    id = String()
    links = List(Nested(LinkSchema))
    skill = Dict()
    proficiency_level = Dict()
    name = String()


class PositionSchema(Schema):

    id = String()


class AvailabilitySchema(Schema):

    time_slots = List(Nested(List(String)))
    time_zone = String()


class CurrencySchema(Schema):

    code = String()


class CompensationSchema(Schema):
    is_fulltime = Boolean()

    hours_per_week = Integer()

    is_salaried = Boolean()

    pay_rate = Integer()

    currency = Nested(CurrencySchema)


class HireDetailsSchema(Schema):

    company = Dict()
    offer_date = DateTime()
    accept_date = DateTime()
    hire_date = DateTime()
    start_date = DateTime()
    full_time_equivalent = Integer()


class EmployeeReferralSchema(Schema):

    name = String(allow_none=True)
    email = String(allow_none=True)
    phone = String(allow_none=True)


class NameSchema(Schema):
    # Was using this but there are all kinds of language text fields

    en_us = String(allow_none=True)
    en_gb = String(allow_none=True)
    en_ca = String(allow_none=True)


class QuestionSchema(Schema):

    id = String()
    type = String()

    text = Dict(allow_none=True)
    potential_score = Integer(allow_none=True)
    creation_method = String(allow_none=True)


class ScreeningQuestionSchema(Schema):

    id = String()
    type = String()

    question = Nested(QuestionSchema)

    response = String(allow_none=True)
    responses = List(String, allow_none=True)

    is_correct = Boolean(allow_none=True)
    is_disqualifying = Boolean(allow_none=True)
    actual_score = Integer()
    recruiter_declined_to_answer = Boolean()


class CountryQuestionSchema(Schema):

    id = String()
    type = String()
    question = Nested(QuestionSchema)
    response = String(allow_none=True)
    responses = List(Dict, allow_none=True)

    # Docs don't show these but they come in
    date = DateTime(allow_none=True)
    name = String(allow_none=True)


class JobBoardSchema(Schema):

    id = String()


class OriginSchema(Schema):

    code = String()


class MotivationSchema(Schema):

    id = String()
    links = List(Nested(LinkSchema))


class OpportunitySchema(Schema):

    id = String()


class ApplicantSourceSchema(Schema):

    id = String(allow_none=True) # Docs do not say nullable but the data is nulled.
    name = Dict()


class LicenseSchema(Schema):

    id = String()
    links = List(Nested(LinkSchema))


class DegreeSchema(Schema):

    id = String()
    name = Dict()
    href = String()
    rel = String()


class SchoolSchema(Schema):

    id = String()
    name = Dict()
    links = List(Nested(LinkSchema))


class MajorSchema(Schema):

    id = String()
    name = String()
    links = List(Nested(LinkSchema))


class MinorSchema(Schema):

    id = String()
    name = String()
    links = List(Nested(LinkSchema))


class EducationSchema(Schema):

    id = String()
    links = List(Nested(LinkSchema))
    degree = Nested(DegreeSchema)
    school = Nested(SchoolSchema)
    major = Nested(MajorSchema, allow_none=True)
    minor = Nested(MajorSchema, allow_none=True)
    from_ = Nested(FromSchema, data_key='from', allow_none=True)
    to = Nested(FromSchema, allow_none=True)
    description = String(allow_none=True)


class ApplicationSchema(Schema):

    id = String()
    links = List(Nested(LinkSchema))
    external_apply_id = String()
    updated_at = DateTime()
    creator = Nested(CreatorSchema)
    recruiting_process = Nested(RecruitingProcessSchema)
    available_start_date = DateTime()
    candidate = Nested(CandidateSchema)
    work_experiences = List(Nested(WorkExperienceSchema))
    is_processed = Boolean()
    educations = List(Nested(EducationSchema))
    job_posting = Nested(JobPostingSchema, allow_none=True)
    skills = List(Nested(SkillSchema))
    position = Nested(PositionSchema, allow_none=True)
    availability = Dict(allow_none=True)
    hire_details = Dict(allow_none=True)
    employee_referral = Nested(EmployeeReferralSchema, allow_none=True)
    country_question_responses = List(Dict, allow_none=True)
    job_board = Nested(JobBoardSchema, allow_none=True)
    applied_date = DateTime()
    origin = Nested(OriginSchema, allow_none=True)
    motivations = List(Nested(MotivationSchema))
    opportunity = Nested(OpportunitySchema)
    applicant_source = Nested(ApplicantSourceSchema)
    licenses = List(Nested(LicenseSchema))
    likes = List(String)
    behaviors = List(String)
    screening_question_responses = List(Nested(ScreeningQuestionSchema))
    creation_method = String()
    external_apply_id = String(allow_none=True)


class Client:

    def __init__(self, hostname, tenant, client_id, client_secret, access_token):
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

    def raise_or_json(self, url):
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def try_json(self, url):
        response = requests.get(url, headers=self.headers)
        if response.ok:
            return response.json()

    def download(self, url, **kwargs):
        headers = {
            'Authorization': f'Bearer {self.access_token}'
        }
        kwargs.update({'headers': headers})
        return requests.get(url, **kwargs)

    def get_applicants(self):
        url = f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}/api/applications'
        return self.raise_or_json(url)

    def get_candidate(self, candidate_id):
        url = (
            f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}/api'
            '/candidates/{candidate_id}'
        )
        return self.raise_or_json(url)

    def get_candidates(self):
        url = f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}/api/candidates'
        return self.raise_or_json(url)

    def get_applications(self):
        url = f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}/api/applications'
        return self.raise_or_json(url)

    def get_applications_for_candidate(self, candidate_id):
        url = (
            f'https://{self.hostname}/talent/recruiting/v2/{self.tenant}'
            f'/api/applications/candidate/{candidate_id}'
        )
        return self.raise_or_json(url)


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

def deep_get(dict_, *keys):
    for key in keys:
        dict_ = dict_.get(key)
    return dict_

filename_keys = set([
    'document_type',
    'file_name',
])

def fix_https(href):
    return href.replace('http:', 'https:')

def save_links(client, parent, basedir):
    links = parent.get('links', [])
    if set(['document_type', 'file_name']).issubset(parent):
        filename = parent['file_name']
        # One of the links should be to a "Download"
        for link_data in links:
            if link_data['rel'] == 'Download':
                href = link_data['href']
                href = fix_https(href)
                response = client.download(href, stream=True)
                breakpoint()
                if response:
                    breakpoint()
                else:
                    logger.debug('%r %r', response, link_data)
    elif links:
        # Recurse down each link
        for link_data in links:
            href = link_data.get('href')
            if href:
                logger.debug('following link %s', href)
                obj = client.try_json(href)
                if isinstance(obj, list):
                    for thing in obj:
                        save_links(client, thing, basedir)
                elif isinstance(obj, dict):
                    newdir = os.path.join(basedir, link_data['rel'])
                    save_links(client, obj, newdir)

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('config')
    args = parser.parse_args(argv)

    logging.basicConfig(
        filename = 'instance/logging.txt',
        level = logging.DEBUG,
    )

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

    application_schema = ApplicationSchema()

    signin_data = get_signin(tenant, client_id, client_secret)
    access_token = signin_data['access_token']

    client = Client(hostname, tenant, client_id, client_secret, access_token)

    os.makedirs(apps_dir, exist_ok=True)

    for application in client.get_applications():
        app_dir = os.path.join(apps_dir, application['id'])
        os.makedirs(app_dir, exist_ok=True)

        json_path = os.path.join(app_dir, 'application.json') 

        # Save downloaded data for application
        with open(json_path, 'w') as json_file:
            json.dump(application, json_file)

        save_links(client, application, app_dir)

if __name__ == "__main__":
    main()
