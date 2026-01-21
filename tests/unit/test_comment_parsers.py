# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import pytest

from packit_service.utils import get_packit_commands_from_comment

packit_comment_command_prefix = "/packit"
packit_comment_command_prefix_fedora_ci = "/packit-ci"


def test_parse_build_comment(comment_parser):
    comment = "/packit build"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "build"


def test_parse_copr_build_comment(comment_parser):
    comment = "/packit copr-build"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "copr-build"


def test_parse_build_commit_arg_comment(comment_parser):
    comment = "/packit build --commit some-branch-name"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "build"
    assert args.commit == "some-branch-name"


def test_parse_build_release_arg_comment(comment_parser):
    comment = "/packit build --release some-tag-name"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "build"
    assert args.release == "some-tag-name"


def test_rebuild_failed_comment(comment_parser):
    comment = "/packit rebuild-failed"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "rebuild-failed"


def test_propose_downstream_comment(comment_parser):
    comment = "/packit propose-downstream"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "propose-downstream"


def test_test_comment(comment_parser):
    comment = "/packit test"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "test"


def test_test_commit_comment(comment_parser):
    comment = "/packit test --commit some-branch-name"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "test"
    assert args.commit == "some-branch-name"


def test_test_release_comment(comment_parser):
    comment = "/packit test --release some-tag-name"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "test"
    assert args.release == "some-tag-name"


def test_retest_failed_comment(comment_parser):
    comment = "/packit retest-failed"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "retest-failed"


def test_test_another_pr_build_comment(comment_parser):
    comment = "/packit test namespace/some-repo/#1234"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "test"
    assert args.target == "namespace/some-repo/#1234"


def test_test_identifier_comment(comment_parser):
    comment = "/packit test --identifier job-id"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "test"
    assert args.identifier == "job-id"


def test_test_identifier_short_comment(comment_parser):
    comment = "/packit test -i job-id"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "test"
    assert args.identifier == "job-id"


def test_test_labels_comment(comment_parser):
    comment = "/packit test --labels comma,separated,labels"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "test"
    assert args.labels == ["comma", "separated", "labels"]


def test_test_env_comment(comment_parser):
    comment = "/packit test --env MY_ENV=test"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "test"
    assert args.env == ["MY_ENV=test"]


def test_test_env_twice_comment(comment_parser):
    comment = "/packit test --env MY_ENV=test --env MY_ENV2=test2"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "test"
    assert args.env == ["MY_ENV=test", "MY_ENV2=test2"]


def test_test_env_unset_comment(comment_parser):
    comment = "/packit test --env MY_ENV="
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "test"
    assert args.env == ["MY_ENV="]


def test_upstream_koji_build_comment(comment_parser):
    comment = "/packit upstream-koji-build"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "upstream-koji-build"


def test_vm_image_build_comment(comment_parser):
    comment = "/packit vm-image-build"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "vm-image-build"


def test_pull_from_upstream_comment(comment_parser):
    comment = "/packit pull-from-upstream"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "pull-from-upstream"


def test_pull_from_upstream_with_pr_config_comment(comment_parser):
    comment = "/packit pull-from-upstream --with-pr-config"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "pull-from-upstream"
    assert args.with_pr_config


def test_pull_from_upstream_resolve_bug_comment(comment_parser):
    comment = "/packit pull-from-upstream --resolve-bug rhbz#123,rhbz#124"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "pull-from-upstream"
    assert args.resolve_bug == ["rhbz#123", "rhbz#124"]


def test_pull_from_upstream_resolve_bug_with_pr_config_comment(comment_parser):
    comment = "/packit pull-from-upstream --with-pr-config --resolve-bug rhbz#123,rhbz#124"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "pull-from-upstream"
    assert args.resolve_bug == ["rhbz#123", "rhbz#124"]
    assert args.with_pr_config


def test_koji_build_comment(comment_parser):
    comment = "/packit koji-build"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "koji-build"


def test_bodhi_update_comment(comment_parser):
    comment = "/packit create-update"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix)

    args = comment_parser.parse_args(commands)
    assert args.command == "create-update"


def test_scratch_build_comment_fedora_ci(comment_parser_fedora_ci):
    comment = "/packit-ci scratch-build"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix_fedora_ci)

    args = comment_parser_fedora_ci.parse_args(commands)
    assert args.command == "scratch-build"


def test_test_comment_fedora_ci(comment_parser_fedora_ci):
    comment = "/packit-ci test"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix_fedora_ci)

    args = comment_parser_fedora_ci.parse_args(commands)
    assert args.command == "test"


def test_test_installability_comment_fedora_ci(comment_parser_fedora_ci):
    comment = "/packit-ci test installability"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix_fedora_ci)

    args = comment_parser_fedora_ci.parse_args(commands)
    assert args.command == "test"
    assert args.target == "installability"


def test_test_rpmlint_comment_fedora_ci(comment_parser_fedora_ci):
    comment = "/packit-ci test rpmlint"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix_fedora_ci)

    args = comment_parser_fedora_ci.parse_args(commands)
    assert args.command == "test"
    assert args.target == "rpmlint"


def test_test_rpminspect_comment_fedora_ci(comment_parser_fedora_ci):
    comment = "/packit-ci test rpminspect"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix_fedora_ci)

    args = comment_parser_fedora_ci.parse_args(commands)
    assert args.command == "test"
    assert args.target == "rpminspect"


def test_test_custom_comment_fedora_ci(comment_parser_fedora_ci):
    comment = "/packit-ci test custom"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix_fedora_ci)

    args = comment_parser_fedora_ci.parse_args(commands)
    assert args.command == "test"
    assert args.target == "custom"


def test_test_unsupported_comment_fedora_ci(comment_parser_fedora_ci):
    comment = "/packit-ci test some-unsupported-test"
    commands = get_packit_commands_from_comment(comment, packit_comment_command_prefix_fedora_ci)

    with pytest.raises(SystemExit):
        comment_parser_fedora_ci.parse_args(commands)
