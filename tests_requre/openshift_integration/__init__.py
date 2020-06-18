from requre.helpers.files import StoreFiles
from requre.helpers.simple_object import Simple
from requre.helpers.git.pushinfo import PushInfoStorageList
from requre.helpers.requests_response import RequestResponseHandling
from requre.helpers.tempfile import TempFile
from requre.import_system import upgrade_import_system
from requre.helpers.git.fetchinfo import RemoteFetch

FILTERS = (
    upgrade_import_system()
    .decorate(
        where="download_helper",
        what="DownloadHelper.request",
        who_name="lookaside_cache_helper",
        decorator=RequestResponseHandling.decorator_plain(),
    )
    .replace_module(where="^tempfile$", who_name="^packit", replacement=TempFile)
    .decorate(
        where="^packit$",
        who_name="fedpkg",
        what="utils.run_command_remote",
        decorator=Simple.decorator_plain(),
    )
    .decorate(
        where="packit.fedpkg",
        what="FedPKG.clone",
        decorator=StoreFiles.where_arg_references(
            key_position_params_dict={"target_path": 2}
        ),
    )
    .decorate(
        where="git",
        who_name="local_project",
        what="remote.Remote.push",
        decorator=PushInfoStorageList.decorator_plain(),
    )
    .decorate(
        where="git",
        who_name="local_project",
        what="remote.Remote.fetch",
        decorator=RemoteFetch.decorator_plain(),
    )
    .decorate(
        where="git",
        who_name="local_project",
        what="remote.Remote.pull",
        decorator=RemoteFetch.decorator_plain(),
    )
    .decorate(  # ogr
        where="^requests$",
        what="Session.send",
        who_name=[
            "ogr.services.pagure",
            "gitlab",
            "github.MainClass",
            "github.Requester",
            "ogr.services.github_tweak",
            "lookaside_cache_helper",
            "^copr",
            "packit.distgit",
        ],
        decorator=RequestResponseHandling.decorator_plain(),
    )
)
