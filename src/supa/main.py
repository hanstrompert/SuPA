#  Copyright 2020 SURF.
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
"""
SuPA main entry point.

SuPA has a single entry point defined in this module,
namely :func:`cli`.
That is what is executed when the ``supa`` command is issued from the command-line.

The other ``@cli.command`` annotated functions in this modules implement the various sub-commands.
"""
from concurrent import futures
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import click
import grpc
import structlog
from click import Context, Option

from supa import init_app, settings
from supa.connection.provider.server import ConnectionProviderService
from supa.grpc_nsi import connection_provider_pb2_grpc

logger = structlog.get_logger(__name__)

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "show_default": True}


# Note: The `type: ignore`'s is the file are their to circumenvent Click's lack of type annotations.


@dataclass
class CommonOptionsState:
    """Class to capture common options shared between Click callables/sub commands."""

    database_file: Optional[Path] = None
    scheduler_max_workers: Optional[int] = None
    domain_name: Optional[str] = None


# Trick from: https://github.com/pallets/click/issues/108
pass_common_options_state = click.make_pass_decorator(CommonOptionsState, ensure=True)
"""Define custom decorator to pass in an CommonOptionState instance as first argument.

When decorating a Click callable/sub command with the :func`common_options` decorator
the Click options defined in that decorator will become part of the sub command
as if they where defined directly on the sub command.
These common options however will not be pass in the argument list to the sub command.
Reason being that we don't know beforehand how many extra common options :func:`common_options` defines,
or if that number later changes possibly breaking existing code.
Instead we want a single state capturing object to be passed in.

Usage::

    @cli.command(context_settings=CONTEXT_SETTINGS)
    @click.option("--fu", ...)
    @click.option("--bar",...)
    @common_options              # <--- usage
    @pass_common_options_state   # <--- usage
    def my_sub_command(common_options: CommonOptionsState, fu: str, bar: str) -> None:
        # explicitly update ``settings`' attributes if they match command line options
        settings.fu = fu
        settings.bar = bar
        ...
        # with all settings resolved, we can now initialize the application properly.
        init_app()

        # actual sub command stuff
        ...
"""


def database_file_option(f):  # type: ignore
    """Define common option for specifying database file location."""

    def callback(ctx: Context, param: Option, value: Optional[str]) -> Optional[str]:
        """Update the Settings instance when the database-file option is used."""
        cos: CommonOptionsState = ctx.ensure_object(CommonOptionsState)
        if value is not None:
            cos.database_file = Path(value)

            # Update the `settings` instance so that it available application wide.
            settings.database_file = cos.database_file
        return value

    return click.option(
        "--database-file",
        type=click.Path(readable=False),
        expose_value=False,  # Don't add to sub command arg list. We have `@pass_common_options_state` for that.
        help="Location of the SQLlite database file",
        callback=callback,
    )(f)


def scheduler_max_workers_option(f):  # type: ignore
    """Define common option for specifying the maximum number of workers to execute scheduler jobs."""

    def callback(ctx: Context, param: Option, value: Optional[int]) -> Optional[int]:
        """Update the Settings instance when the scheduler-max-workers option is used."""
        cos: CommonOptionsState = ctx.ensure_object(CommonOptionsState)
        if value is not None:
            cos.scheduler_max_workers = value

            # Update the `settings` instance so that it available application wide.
            settings.scheduler_max_workers = cos.scheduler_max_workers
        return value

    return click.option(
        "--scheduler-max-workers",
        default=settings.scheduler_max_workers,
        type=int,
        expose_value=False,  # Don't add to sub command arg list. We have `@pass_common_options_state` for that.
        help="Maximum number of workers to execute scheduler jobs.",
        callback=callback,
    )(f)


def domain_name_option(f):  # type: ignore
    """Define common option for specifying the name of the network SuPA is responsible for."""

    def callback(ctx: Context, param: Option, value: Optional[str]) -> Optional[str]:
        """Update the Settings instance when the domain-name option is used."""
        cos: CommonOptionsState = ctx.ensure_object(CommonOptionsState)
        if value is not None:
            cos.domain_name = value

            # Update the `settings` instance so that it available application wide.
            settings.domain_name = cos.domain_name
        return value

    return click.option(
        "--domain-name",
        default=settings.domain_name,
        type=str,
        expose_value=False,  # Don't add to sub command arg list. We have `@pass_common_options_state` for that.
        help="Name of the domain SuPA is responsible for.",
        callback=callback,
    )(f)


def common_options(f):  # type: ignore
    """Provide the means to declare common options to Click callables/sub command."""
    f = database_file_option(f)
    f = scheduler_max_workers_option(f)
    f = domain_name_option(f)
    return f


@click.group(context_settings=CONTEXT_SETTINGS)
def cli() -> None:
    """Manage the SURF ultimate Provider Agent from the command line.

    Configuration variables can be set using (in order of precedence):

    \b
    - command line options
    - environment variables
    - entries in `supa.env`

    For more information see `supa.env`.
    """
    pass


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--grpc-max-workers",
    default=settings.grpc_max_workers,
    type=int,
    help="Maximum number of workers to serve gRPC requests.",
)
@click.option("--grpc-insecure-address-port", default=settings.grpc_insecure_address_port, help="Port to listen on.")
@common_options  # type: ignore
def serve(grpc_max_workers: int, grpc_insecure_address_port: str) -> None:
    """Start the gRPC server and listen for incoming requests."""
    # Command-line options take precedence.
    settings.grpc_max_workers = grpc_max_workers
    settings.grpc_insecure_address_port = grpc_insecure_address_port

    init_app()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=settings.grpc_max_workers))
    log = logger.bind(grpc_max_workers=settings.grpc_max_workers)

    connection_provider_pb2_grpc.add_ConnectionProviderServicer_to_server(ConnectionProviderService(), server)
    server.add_insecure_port(settings.grpc_insecure_address_port)
    log = log.bind(grpc_insecure_address_port=settings.grpc_insecure_address_port)

    server.start()
    log.info("Started Connection Provider gRPC Service.")

    server.wait_for_termination()
