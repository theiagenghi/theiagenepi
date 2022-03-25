import datetime
import json
import os
import re
import threading
from typing import Any, List, Mapping, Optional, Sequence, Set, Union

import sentry_sdk
import sqlalchemy as sa
from boto3 import Session
from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncResult, AsyncSession
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.exc import NoResultFound
from starlette.requests import Request

from aspen.api.auth import get_auth_user
from aspen.api.deps import get_db, get_settings
from aspen.api.error import http_exceptions as ex
from aspen.api.schemas.samples import (
    CreateSampleRequest,
    SampleBulkDeleteRequest,
    SampleBulkDeleteResponse,
    SampleDeleteResponse,
    SampleResponse,
    SamplesResponse,
    UpdateSamplesRequest,
    ValidateIDsRequest,
    ValidateIDsResponse,
)
from aspen.api.settings import Settings
from aspen.api.utils import (
    authz_samples_cansee,
    check_duplicate_samples,
    check_duplicate_samples_in_request,
    determine_gisaid_status,
    get_matching_gisaid_ids,
    get_missing_and_found_sample_ids,
)
from aspen.database.models import (
    DataType,
    Location,
    Sample,
    UploadedPathogenGenome,
    User,
)

router = APIRouter()

GISAID_REJECTION_TIME = datetime.timedelta(days=4)


@router.get("/")
async def list_samples(
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_auth_user),
) -> SamplesResponse:

    cansee_groups_private_identifiers: Set[int] = {
        cansee.owner_group_id
        for cansee in user.group.can_see
        if cansee.data_type == DataType.PRIVATE_IDENTIFIERS
    }

    # load the samples.
    all_samples_query = sa.select(Sample).options(  # type: ignore
        selectinload(Sample.uploaded_pathogen_genome),
        selectinload(Sample.submitting_group),
        selectinload(Sample.uploaded_by),
        selectinload(Sample.collection_location),
        selectinload(Sample.accessions),
    )
    user_visible_samples_query = authz_samples_cansee(all_samples_query, None, user)
    user_visible_samples_result = await db.execute(user_visible_samples_query)
    user_visible_samples: List[Sample] = (
        user_visible_samples_result.unique().scalars().all()
    )

    # populate sample object using pydantic response schema
    result = SamplesResponse(samples=[])
    for sample in user_visible_samples:
        sample.gisaid = determine_gisaid_status(
            sample,
        )
        sample.show_private_identifier = False
        if (
            sample.submitting_group_id == user.group_id
            or sample.submitting_group_id in cansee_groups_private_identifiers
            or user.system_admin
        ):
            sample.show_private_identifier = True

        sampleinfo = SampleResponse.from_orm(sample)
        result.samples.append(sampleinfo)
    return result


async def get_owned_samples_by_ids(
    db: AsyncSession, sample_ids: List[int], user: User
) -> AsyncResult:
    query = (
        sa.select(Sample)  # type: ignore
        .options(
            joinedload(Sample.uploaded_pathogen_genome),
            joinedload(Sample.submitting_group),
            joinedload(Sample.uploaded_by),
            joinedload(Sample.collection_location),
        )
        .filter(  # type: ignore
            sa.and_(
                Sample.submitting_group
                == user.group,  # This is an access control check!
                Sample.id.in_(sample_ids),
            )
        )
    )
    results = await db.execute(query)
    return results.scalars()


@router.delete("/")
async def delete_samples(
    sample_info: SampleBulkDeleteRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_auth_user),
) -> SampleDeleteResponse:
    # Make sure this sample exists and is delete-able by the current user.
    samples_res = await get_owned_samples_by_ids(db, sample_info.ids, user)
    samples = samples_res.all()
    if len(samples) != len(sample_info.ids):
        raise ex.NotFoundException("samples not found")

    db_ids = []
    for sample in samples:
        db_ids.append(sample.id)
        await db.delete(sample)

    await db.commit()
    return SampleBulkDeleteResponse(ids=db_ids)


@router.delete("/{sample_id}")
async def delete_sample(
    sample_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_auth_user),
) -> SampleDeleteResponse:
    # Make sure this sample exists and is delete-able by the current user.
    sample_db_res = await get_owned_samples_by_ids(db, [sample_id], user)
    try:
        sample = sample_db_res.one()
    except NoResultFound:
        raise ex.NotFoundException("sample not found")

    sample_db_id = sample.id
    await db.delete(sample)
    await db.commit()
    return SampleDeleteResponse(id=sample_db_id)


@router.put("/")
async def update_samples(
    update_samples_request: UpdateSamplesRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_auth_user),
) -> SamplesResponse:

    # reorganize request data to make it easier to update
    reorganized_request_data = {s.id: s for s in update_samples_request.samples}
    sample_ids_to_update = list(reorganized_request_data.keys())

    # Make sure these samples exist and are delete-able by the current user.
    sample_db_res = await get_owned_samples_by_ids(db, sample_ids_to_update, user)
    editable_samples = sample_db_res.all()

    # are there any samples that can't be updated?
    uneditable_samples = [
        s for s in sample_ids_to_update if s not in [i.id for i in editable_samples]
    ]
    if uneditable_samples:
        raise ex.NotFoundException("some samples cannot be updated")

    res = SamplesResponse(samples=[])
    for sample in editable_samples:
        update_data = reorganized_request_data[sample.id]
        for key, value in update_data:
            if key in ["collection_location", "sequencing_date"]:
                continue
            if value is not None:  # We need to be able to set private to False!
                setattr(sample, key, value)
        # Location id is handled specially
        if update_data.collection_location:
            loc = await db.get(Location, update_data.collection_location)
            if not loc:
                raise ex.BadRequestException("location is invalid")
            sample.collection_location = loc
        # Sequencing date is handled specially
        if update_data.sequencing_date:
            sample.uploaded_pathogen_genome.sequencing_date = (
                update_data.sequencing_date
            )
        sample.show_private_identifier = True
        res.samples.append(SampleResponse.from_orm(sample))

    try:
        await db.commit()
    except IntegrityError:
        # We're relying on Posgres' group+private_id and group+public_id uniqueness
        # constraints to check whether we have duplicate identifiers.
        raise ex.BadRequestException(
            "All private and public identifiers must be unique"
        )

    return res


@router.post("/validate_ids/")
async def validate_ids(
    request_data: ValidateIDsRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_auth_user),
) -> ValidateIDsResponse:

    """
    take in a list of identifiers and checks if all idenitifiers exist as either Sample public or private identifiers, or GisaidMetadata strain names

    returns a response with list of missing identifiers if any, otherwise will return an empty list
    """

    sample_ids: Set[str] = {item for item in request_data.sample_ids}

    all_samples_query = sa.select(Sample).options()  # type: ignore

    # get all samples from request that the user has permission to use and scope down
    # the search for matching ID's to groups that the user has read access to.
    user_visible_samples_query = authz_samples_cansee(
        all_samples_query, sample_ids, user
    )
    user_visible_samples_res = await (db.execute(user_visible_samples_query))
    user_visible_samples = user_visible_samples_res.scalars().all()

    # Are there any sample ID's that don't match sample table public and private identifiers
    missing_sample_ids, _ = get_missing_and_found_sample_ids(
        sample_ids, user_visible_samples
    )

    # See if these missing_sample_ids match any Gisaid identifiers
    gisaid_ids: Set[str] = await get_matching_gisaid_ids(db, missing_sample_ids)

    # Do we have any samples that are not aspen private or public identifiers or gisaid identifiers?
    missing_sample_ids -= gisaid_ids

    return ValidateIDsResponse(missing_sample_ids=missing_sample_ids)


# TODO this should be a general swipe calling library instead of lame copy-pasta.
def _kick_off_pangolin(group_prefix: str, sample_ids: Sequence[str], settings):
    sfn_params = settings.AWS_PANGOLIN_SFN_PARAMETERS
    sfn_input_json = {
        "Input": {
            "Run": {
                "aws_region": settings.AWS_REGION,
                "docker_image_id": sfn_params["Input"]["Run"]["docker_image_id"],
                "samples": sample_ids,
                "remote_dev_prefix": settings.REMOTE_DEV_PREFIX,
                "genepi_config_secret_name": settings.GENEPI_CONFIG_SECRET_NAME,
            },
        },
        "OutputPrefix": f"{sfn_params['OutputPrefix']}",
        "RUN_WDL_URI": sfn_params["RUN_WDL_URI"],
        "RunEC2Memory": sfn_params["RunEC2Memory"],
        "RunEC2Vcpu": sfn_params["RunEC2Vcpu"],
        "RunSPOTMemory": sfn_params["RunSPOTMemory"],
        "RunSPOTVcpu": sfn_params["RunSPOTVcpu"],
    }

    session = Session(region_name=settings.AWS_REGION)
    client = session.client(
        service_name="stepfunctions",
        endpoint_url=os.getenv("BOTO_ENDPOINT_URL") or None,
    )

    execution_name = f"{group_prefix}-ondemand-pangolin-{str(datetime.datetime.now())}"
    execution_name = re.sub(r"[^0-9a-zA-Z-]", r"-", execution_name)

    client.start_execution(
        stateMachineArn=sfn_params["StateMachineArn"],
        name=execution_name,
        input=json.dumps(sfn_input_json),
    )


@router.post("/")
async def create_samples(
    create_samples_request: List[CreateSampleRequest],
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_auth_user),
) -> SamplesResponse:

    duplicates_in_request: Union[
        None, Mapping[str, list[str]]
    ] = check_duplicate_samples_in_request(create_samples_request)
    if duplicates_in_request:
        raise ex.BadRequestException(
            f"Error processing data, either duplicate private_identifiers: {duplicates_in_request['duplicate_private_ids']} or duplicate public identifiers: {duplicates_in_request['duplicate_public_ids']} exist in the upload files, please rename duplicates before proceeding with upload.",
        )

    already_exists: Union[
        None, Mapping[str, list[str]]
    ] = await check_duplicate_samples(create_samples_request, db, user.group_id)
    if already_exists:
        raise ex.BadRequestException(
            f"Error inserting data, private_identifiers {already_exists['existing_private_ids']} or public_identifiers: {already_exists['existing_public_ids']} already exist in our database, please remove these samples before proceeding with upload.",
        )

    created_samples = []
    for row in create_samples_request:
        sample_input = row.sample
        pathogen_genome_input = row.pathogen_genome

        valid_location: Optional[Location] = await Location.get_by_id(
            db, sample_input.location_id
        )
        if not valid_location:
            sentry_sdk.capture_message(
                f"No valid location for id {sample_input.location_id}"
            )
            raise ex.BadRequestException("Invalid location id for sample")

        sample_args: Mapping[str, Any] = {
            "submitting_group": user.group,
            "uploaded_by": user,
            "sample_collected_by": user.group.name,
            "sample_collector_contact_address": user.group.address,
            "organism": sample_input.organism,
            "private_identifier": sample_input.private_identifier,
            "collection_date": sample_input.collection_date,
            "private": sample_input.private,
            "public_identifier": sample_input.public_identifier,
            "authors": sample_input.authors or [user.group.name],
            "collection_location": valid_location,
        }
        if sample_input.imported_by:
            sample_args["imported_at"] = datetime.datetime.now()
            sample_args["imported_by"] = sample_input.imported_by

        sample: Sample = Sample(**sample_args)
        sample.generate_public_identifier()
        uploaded_pathogen_genome: UploadedPathogenGenome = UploadedPathogenGenome(
            sample=sample,
            sequence=pathogen_genome_input.sequence,
            sequencing_date=pathogen_genome_input.sequencing_date,
        )

        db.add(sample)
        db.add(uploaded_pathogen_genome)
        created_samples.append(sample)

    # Write all of our rows to the DB inside our transaction
    await db.flush()

    # Read the samples back from the DB with all fields populated.
    new_samples_query = (
        sa.select(Sample)  # type: ignore
        .options(  # type: ignore
            selectinload(Sample.uploaded_pathogen_genome),
            selectinload(Sample.submitting_group),
            selectinload(Sample.uploaded_by),
            selectinload(Sample.collection_location),
            selectinload(Sample.accessions),
        )
        .filter(Sample.id.in_([sample.id for sample in created_samples]))
        .execution_options(populate_existing=True)
    )
    res = await db.execute(new_samples_query)

    pangolin_sample_ids = []
    result = SamplesResponse(samples=[])
    for sample in res.unique().scalars().all():
        pangolin_sample_ids.append(sample.public_identifier)
        sample.gisaid = determine_gisaid_status(
            sample,
        )
        sample.show_private_identifier = True
        sampleinfo = SampleResponse.from_orm(sample)
        result.samples.append(sampleinfo)

    await db.commit()

    # Run as a separate thread, so any errors here won't affect sample uploads
    pangolin_job = threading.Thread(
        target=_kick_off_pangolin,
        args=(user.group.prefix, pangolin_sample_ids, settings),
    )
    pangolin_job.start()

    return result
