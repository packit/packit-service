#!/usr/bin/python3

# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import argparse

from sqlalchemy import create_engine, delete, distinct, func, select, union

from packit_service.models import (
    CoprBuildGroupModel,
    CoprBuildTargetModel,
    GitBranchModel,
    GitProjectModel,
    IssueModel,
    JobTriggerModel,
    JobTriggerModelType,
    KojiBuildGroupModel,
    KojiBuildTargetModel,
    PipelineModel,
    ProjectAuthenticationIssueModel,
    ProjectReleaseModel,
    PullRequestModel,
    SRPMBuildModel,
    SyncReleaseModel,
    SyncReleaseTargetModel,
    TFTTestRunGroupModel,
    TFTTestRunTargetModel,
    VMImageBuildTargetModel,
    get_pg_url,
    tf_copr_association_table,
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="""\
Remove old data from the DB in order to speed up queries.

Set POSTGRESQL_* environment variables to define the DB URL.
See get_pg_url() for details.
""",
    )
    parser.add_argument(
        "age",
        type=str,
        nargs="?",
        default="1 year",
        help="Remove data older than this. For example: "
        "'1 year' or '6 months'. Defaults to '1 year'.",
    )
    args = parser.parse_args()

    engine = create_engine(
        get_pg_url(),
        echo=True,
    )
    with engine.begin() as conn:
        # Delete the pipelines older than AGE
        stmt = delete(PipelineModel).where(func.age(PipelineModel.datetime) >= args.age)
        conn.execute(stmt)

        # Delete JobTriggers, SRPMBuilds and VMImageBuilds which don't belong to a pipeline
        attr = [
            (JobTriggerModel, PipelineModel.job_trigger_id),
            (SRPMBuildModel, PipelineModel.srpm_build_id),
            (VMImageBuildTargetModel, PipelineModel.vm_image_build_id),
        ]
        for model, field in attr:
            orphaned = (
                select(distinct(model.id))
                .outerjoin(PipelineModel, field == model.id)
                .filter(PipelineModel.id == None)  # noqa
            )
            stmt = delete(model).where(model.id.in_(orphaned))
            conn.execute(stmt)

        # Delete CoprBuildTargets and tf-copr associations which don't belong to a pipeline
        orphaned = (
            select(distinct(CoprBuildTargetModel.id))
            .outerjoin_from(
                CoprBuildGroupModel,
                CoprBuildTargetModel,
                CoprBuildTargetModel.copr_build_group_id == CoprBuildGroupModel.id,
            )
            .outerjoin(
                PipelineModel,
                PipelineModel.copr_build_group_id == CoprBuildGroupModel.id,
            )
            .filter(PipelineModel.id == None)  # noqa
        )
        stmt = delete(tf_copr_association_table).where(
            tf_copr_association_table.c.copr_id.in_(orphaned),
        )
        conn.execute(stmt)
        stmt = delete(CoprBuildTargetModel).where(CoprBuildTargetModel.id.in_(orphaned))
        conn.execute(stmt)

        # Delete KojiBuildTargets, TFTTestRunTargets and SyncReleaseTargets
        # which don't belong to any pipeline
        attr = [
            (
                KojiBuildTargetModel,
                KojiBuildGroupModel,
                PipelineModel.koji_build_group_id,
                KojiBuildTargetModel.koji_build_group_id,
            ),
            (
                TFTTestRunTargetModel,
                TFTTestRunGroupModel,
                PipelineModel.test_run_group_id,
                TFTTestRunTargetModel.tft_test_run_group_id,
            ),
            (
                SyncReleaseTargetModel,
                SyncReleaseModel,
                PipelineModel.sync_release_run_id,
                SyncReleaseTargetModel.sync_release_id,
            ),
        ]
        for target_m, group_m, id_f, model_group_id in attr:
            print(f"Working on {target_m}...")
            orphaned = (
                select(distinct(target_m.id))
                .outerjoin_from(group_m, target_m, model_group_id == group_m.id)
                .outerjoin(PipelineModel, id_f == group_m.id)
                .filter(PipelineModel.id == None)  # noqa
            )
            stmt = delete(target_m).where(target_m.id.in_(orphaned))
            conn.execute(stmt)

        # Now that the targets are all cleaned up, let's get rid of the Groups
        # which don't reference any targets and are not referenced by any pipeline.
        #   - CoprBuildGroups
        #   - KojiBuildGroups
        #   - TFTTestRunGroups
        #   - SynReleaseRuns
        groups = [
            (
                CoprBuildGroupModel,
                CoprBuildTargetModel,
                CoprBuildTargetModel.copr_build_group_id,
                PipelineModel.copr_build_group_id,
            ),
            (
                KojiBuildGroupModel,
                KojiBuildTargetModel,
                KojiBuildTargetModel.koji_build_group_id,
                PipelineModel.koji_build_group_id,
            ),
            (
                TFTTestRunGroupModel,
                TFTTestRunTargetModel,
                TFTTestRunTargetModel.tft_test_run_group_id,
                PipelineModel.test_run_group_id,
            ),
        ]
        for group, target, target_group_id, pipeline_group_id in groups:
            orphaned_groups = (
                select(distinct(group.id))
                .outerjoin(
                    target,
                    group.id == target_group_id,
                )
                .outerjoin(
                    PipelineModel,
                    pipeline_group_id == group.id,
                )
                .filter(target.id == None)  # noqa
                .filter(PipelineModel.id == None)  # noqa
            )
            stmt = delete(group).where(group.id.in_(orphaned_groups))
            conn.execute(stmt)

        # Delete the trigger objects which are not referenced by any JobTriggers
        #   - PullRequestModel
        #   - GitBranchModel
        #   - ProjectReleaseModel
        #   - IssueModel

        trigger_types = [
            (JobTriggerModelType.pull_request, PullRequestModel),
            (JobTriggerModelType.branch_push, GitBranchModel),
            (JobTriggerModelType.release, ProjectReleaseModel),
            (JobTriggerModelType.issue, IssueModel),
        ]
        for trigger_type, trigger_model in trigger_types:
            triggers = (
                select(JobTriggerModel).filter(JobTriggerModel.type == trigger_type).subquery()
            )
            orphaned_triggers = (
                select(trigger_model.id)
                .outerjoin(triggers, trigger_model.id == triggers.columns.trigger_id)
                .filter(triggers.columns.trigger_id == None)  # noqa
            )
            stmt = delete(trigger_model).where(trigger_model.id.in_(orphaned_triggers))
            conn.execute(stmt)

        # Delete the GitProjectModel not referenced by anything
        # - PullRequestModel
        # - GitBranchModel
        # - ProjectReleaseModel
        # - IssueModel
        # - ProjectAuthenticationIssueModel
        referenced_projects = union(
            select(PullRequestModel.project_id),
            select(GitBranchModel.project_id),
            select(ProjectReleaseModel.project_id),
            select(IssueModel.project_id),
            select(ProjectAuthenticationIssueModel.project_id),
        )
        stmt = delete(GitProjectModel).where(
            GitProjectModel.id.not_in(referenced_projects),
        )
        conn.execute(stmt)
