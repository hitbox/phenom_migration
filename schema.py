import marshmallow as mm

class LocalizedReferenceSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    id = mm.fields.String()

    # name is dict with keys like language_country code (en_us) and value like:
    # Mr., Mrs., Jr., etc.
    name = mm.fields.Raw()


class NameSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    first = mm.fields.String(allow_none=True)
    middle = mm.fields.String(allow_none=True)
    last = mm.fields.String(allow_none=True)
    former = mm.fields.String(allow_none=True)

    # Sometimes a string, null or a whole other object!
    prefix = mm.fields.Nested(LocalizedReferenceSchema, allow_none=True)
    suffix = mm.fields.Nested(LocalizedReferenceSchema, allow_none=True)


class PhoneSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    pimary = mm.fields.String(allow_none=True)


class CountryNameSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE


class CountrySchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    code = mm.fields.String()
    id = mm.fields.String()
    name = mm.fields.Dict()


class StateSchema(mm.Schema):

    code = mm.fields.String()
    name = mm.fields.Dict()


class AddressSchema(mm.Schema):

    city = mm.fields.String()
    country = mm.fields.Dict()
    state = mm.fields.Nested(StateSchema)


class ContactInfoSchema(mm.Schema):

    email = mm.fields.String()
    phone = mm.fields.Nested(PhoneSchema, allow_none=True)

    # NOTE
    # - address can be just about anything!
    address = mm.fields.Dict(allow_none=True)


class CandidateSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    id = mm.fields.String()
    is_internal = mm.fields.Boolean()
    is_active = mm.fields.Boolean()
    employment_status = mm.fields.String(allow_none=True)
    name = mm.fields.Nested(NameSchema)

    created_at = mm.fields.DateTime()
    updated_at = mm.fields.DateTime()

    contact_info = mm.fields.Nested(ContactInfoSchema)


class ReferenceIdSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    id = mm.fields.String(allow_none=True)


class DateRangePartSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    month = mm.fields.Integer(allow_none=True)
    year = mm.fields.Integer(allow_none=True)


class WorkExperienceSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    id = mm.fields.String()
    job_title = mm.fields.String(allow_none=True)
    company = mm.fields.String(allow_none=True)
    location = mm.fields.String(allow_none=True)
    from_ = mm.fields.Nested(
        DateRangePartSchema,
        data_key="from",
        allow_none=True
    )
    to = mm.fields.Nested(
        DateRangePartSchema,
        allow_none=True
    )
    description = mm.fields.String(allow_none=True)


class SkillSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    id = mm.fields.String()
    skill = mm.fields.Nested(ReferenceIdSchema)
    proficiency_level = mm.fields.Dict()


class LinkSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    href = mm.fields.String()
    rel = mm.fields.String()


class LinkResultSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    id = mm.fields.String()
    file_name = mm.fields.String()
    document_type = mm.fields.String()
    description = mm.fields.String(allow_none=True)

    creator = mm.fields.Raw()

    links = mm.fields.List(
        mm.fields.Nested(LinkSchema)
    )

    @mm.post_load
    def mark_loaded(self, data, **kwargs):
        data['_schema_loaded'] = True
        return data


class DocumentSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    id = mm.fields.String()
    file_name = mm.fields.String(allow_none=True)
    document_type = mm.fields.String(allow_none=True)
    description = mm.fields.String(allow_none=True)

    creator = mm.fields.Nested(ReferenceIdSchema, allow_none=True)

    links = mm.fields.List(
        mm.fields.Dict(),
        allow_none=True
    )


class ApplicantSourceSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    id = mm.fields.String(allow_none=True)
    # Goofy dict: {'en_us': 'Some name'}
    name = mm.fields.Dict()

class ApplicationSchema(mm.Schema):
    class Meta:
        unknown = mm.INCLUDE

    id = mm.fields.String()
    updated_at = mm.fields.DateTime(allow_none=True)
    
    creator = mm.fields.Nested(ReferenceIdSchema)
    candidate = mm.fields.Nested(CandidateSchema, allow_none=True)

    opportunity = mm.fields.Nested(ReferenceIdSchema)
    job_board = mm.fields.Nested(ReferenceIdSchema, allow_none=True)
    job_posting = mm.fields.Nested(ReferenceIdSchema, allow_none=True)

    applicant_source = mm.fields.Nested(ApplicantSourceSchema)

    # could be a string or an object.
    origin = mm.fields.Raw(allow_none=True)

    external_apply_id = mm.fields.String(allow_none=True)
    creation_method = mm.fields.String()
    employee_referral = mm.fields.Dict(allow_none=True)

    availability = mm.fields.Dict(allow_none=True)

    available_start_date = mm.fields.DateTime(allow_none=True)
    applied_date = mm.fields.DateTime(allow_none=True)

    is_processed = mm.fields.Boolean()

    recruiting_process = mm.fields.Dict(allow_none=True)
    hire_details = mm.fields.Dict(allow_none=True)

    screening_question_responses = mm.fields.List(
        mm.fields.Dict()
    )

    country_question_responses = mm.fields.List(
        mm.fields.Dict()
    )

    likes = mm.fields.List(mm.fields.Dict())
    licenses = mm.fields.List(mm.fields.Dict())
    educations = mm.fields.List(mm.fields.Dict())
    behaviors = mm.fields.List(mm.fields.Dict())
    motivations = mm.fields.List(mm.fields.Dict())

    work_experiences = mm.fields.List(
        mm.fields.Nested(WorkExperienceSchema)
    )

    skills = mm.fields.List(
        mm.fields.Nested(SkillSchema)
    )

    position = mm.fields.Dict(allow_none=True)

    links = mm.fields.List(
        mm.fields.Dict()
    )
