from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps

from prowler.lib.logger import logger
from prowler.lib.ui.live_display import live_display
from prowler.providers.aws.aws_provider import (
    generate_regional_clients,
    get_default_region,
)
from prowler.providers.aws.lib.audit_info.models import AWS_Audit_Info
from prowler.providers.aws.aws_provider_new import AwsProvider

MAX_WORKERS = 10


class AWSService:
    """The AWSService class offers a parent class for each AWS Service to generate:
    - AWS Regional Clients
    - Shared information like the account ID and ARN, the the AWS partition and the checks audited
    - AWS Session
    - Thread pool for the __threading_call__
    - Also handles if the AWS Service is Global
    """

    def __init__(self, service: str, provider: AwsProvider, global_service=False):
        # Audit Information
        self.provider = provider
        self.audited_account = provider.identity.account
        self.audited_account_arn = provider.identity.account_arn
        self.audited_partition = provider.identity.partition
        self.audit_resources = provider.audit_resources
        self.audited_checks = provider.audit_metadata.expected_checks
        self.audit_config = provider.audit_config

        # AWS Session
        self.session = provider.session.session

        # We receive the service using __class__.__name__ or the service name in lowercase
        # e.g.: AccessAnalyzer --> we need a lowercase string, so service.lower()
        self.service = service.lower() if not service.islower() else service

        # Generate Regional Clients
        if not global_service:
            self.regional_clients = provider.generate_regional_clients(
                self.service, global_service
            )

        # Get a single region and client if the service needs it (e.g. AWS Global Service)
        # We cannot include this within an else because some services needs both the regional_clients
        # and a single client like S3
        self.region = provider.get_default_region(self.service)
        self.client = self.session.client(self.service, self.region)

        # Thread pool for __threading_call__
        self.thread_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)

        self.live_display_enabled = False
        # Progress bar to add tasks to
        service_init_section = live_display.get_client_init_section()
        if service_init_section:
            # Only Flags is not set to True
            self.task_progress_bar = service_init_section.task_progress_bar
            self.progress_tasks = []
            # For us in other functions
            self.live_display_enabled = True

    def __get_session__(self):
        return self.session

    def __threading_call__(self, call, iterator=None, *args, **kwargs):
        # Use the provided iterator, or default to self.regional_clients
        items = iterator if iterator is not None else self.regional_clients.values()
        # Determine the total count for logging
        item_count = len(items)

        # Trim leading and trailing underscores from the call's name
        call_name = call.__name__.strip("_")
        # Add Capitalization
        call_name = " ".join([x.capitalize() for x in call_name.split("_")])

        # Print a message based on the call's name, and if its regional or processing a list of items
        if iterator is None:
            logger.info(
                f"{self.service.upper()} - Starting threads for '{call_name}' function across {item_count} regions..."
            )
        else:
            logger.info(
                f"{self.service.upper()} - Starting threads for '{call_name}' function to process {item_count} items..."
            )

        if self.live_display_enabled:
            # Setup the progress bar
            task_id = self.task_progress_bar.add_task(
                f"- {call_name}...", total=item_count, task_type="Service"
            )
            self.progress_tasks.append(task_id)

        # Submit tasks to the thread pool
        futures = [
            self.thread_pool.submit(call, item, *args, **kwargs) for item in items
        ]

        # Wait for all tasks to complete
        for future in as_completed(futures):
            try:
                future.result()  # Raises exceptions from the thread, if any
                if self.live_display_enabled:
                    # Update the progress bar
                    self.task_progress_bar.update(task_id, advance=1)
            except Exception:
                # Handle exceptions if necessary
                pass  # Replace 'pass' with any additional exception handling logic. Currently handled within the called function

        # Make the task disappear once completed
        # self.progress.remove_task(task_id)

    @staticmethod
    def progress_decorator(func):
        """
        Decorator to update the progress bar before and after a function call.
        To be used for methods within global services, which do not make use of the __threading_call__ function
        """

        @wraps(func)
        def wrapper(self, *args, **kwargs):
            # Trim leading and trailing underscores from the call's name
            func_name = func.__name__.strip("_")
            # Add Capitalization
            func_name = " ".join([x.capitalize() for x in func_name.split("_")])

            if self.live_display_enabled:
                task_id = self.task_progress_bar.add_task(
                    f"- {func_name}...", total=1, task_type="Service"
                )
                self.progress_tasks.append(task_id)

            result = func(self, *args, **kwargs)  # Execute the function

            if self.live_display_enabled:
                self.task_progress_bar.update(task_id, advance=1)
            # self.task_progress_bar.remove_task(task_id)  # Uncomment if you want to remove the task on completion

            return result

        return wrapper
