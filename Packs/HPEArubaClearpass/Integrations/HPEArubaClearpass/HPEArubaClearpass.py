import sys
from datetime import datetime, timedelta

import demistomock as demisto
from CommonServerPython import *  # noqa # pylint: disable=unused-wildcard-import
from CommonServerUserPython import *  # noqa

import requests
import traceback
from typing import Dict, Any

# Disable insecure warnings
requests.packages.urllib3.disable_warnings()  # pylint: disable=no-member

DATE_FORMAT = '%Y-%m-%dT%H:%M:%SZ'  # ISO8601 format with UTC, default in XSOAR"
TOKEN_TYPE = "Bearer"


class Client(BaseClient):
    def __init__(self, proxy: bool, verify: bool, base_url: str, client_id: str, client_secret: str):
        super().__init__(proxy=proxy, verify=verify, base_url=base_url)
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = ""
        self.headers = {}

    def generate_new_access_token(self):
        body = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret
        }
        return self._http_request(
            method='POST',
            url_suffix="oauth",
            json_data=body
        )

    def save_access_token_to_context(self, auth_response):
        access_token_expiration_in_seconds = auth_response.get("expires_in")
        if access_token_expiration_in_seconds and isinstance(auth_response.get("expires_in"), int):
            access_token_expiration_datetime = datetime.now() + timedelta(seconds=access_token_expiration_in_seconds)
            context = {"access_token": self.access_token, "expires_in": access_token_expiration_datetime}
            set_integration_context(context)
            demisto.debug(
                f"New access token that expires in : {access_token_expiration_datetime.strftime(DATE_FORMAT)} w"
                f"as set to integration_context.")
        else:
            return_error(f"HPEArubaClearpass error: Got an invalid access token "
                         f"expiration time from the API: {access_token_expiration_in_seconds} "
                         f"from type: {type(access_token_expiration_in_seconds)}")

    def login(self):
        integration_context = get_integration_context()
        access_token_expiration = integration_context.get('expires_in')
        access_token = integration_context.get('access_token')
        is_context_has_access_token = access_token and access_token_expiration
        if is_context_has_access_token and access_token_expiration > datetime.now():
            self.set_request_headers()
            return

        # if the access is expired or not exist, generate a new one
        auth_response = self.generate_new_access_token()
        access_token = auth_response.get("access_token")
        if access_token:
            self.access_token = access_token
            self.save_access_token_to_context(auth_response)
            self.set_request_headers()
        else:
            return_error("HPE Aruba Clearpass error: The client credentials are invalid.")

    def set_request_headers(self):
        """
        Setting the headers for the future HTTP requests.
        The headers should be: {Authorization: Bearer <access_token>}
        """
        authorization_header_value = f"{TOKEN_TYPE} {self.access_token}"
        self.headers = {"Authorization": authorization_header_value}

    def request_endpoints(self, method: str, params: dict, url_suffix: str, body={}):
        return self._http_request(
            method=method,
            params=params,
            url_suffix=url_suffix,
            headers=self.headers,
            json_data=body
        )


''' COMMAND FUNCTIONS '''


def test_module(client: Client) -> str:
    """Tests API connectivity and authentication'

    Returning 'ok' indicates that the integration works like it is supposed to.
    Connection to the service is successful.
    Raises exceptions if something goes wrong.

    :type client: ``Client``
    :param Client: client to use

    :return: 'ok' if test passed, anything else will fail the test.
    :rtype: ``str``
    """

    message: str = ''
    try:
        message = 'ok'
    except DemistoException as e:
        if 'Forbidden' in str(e) or 'Authorization' in str(e):
            message = 'Authorization Error: make sure API Key is correctly set'
        else:
            raise e
    return message


def parse_endpoints_response(response):
    items_list = response.get('_embedded', {}).get('items')
    human_readable = []
    if items_list:
        for item in items_list:
            human_readable.append({
                'ID': item.get('id'),
                'MAC Address': item.get('mac_address'),
                'Status': item.get('status'),
                'Attributes': item.get('attributes')
            })
    return human_readable, items_list


def get_endpoints_list_command(client: Client, args: Dict[str, Any]) -> CommandResults:
    mac_address = args.get('mac_address')
    status = args.get('status')
    offset = args.get('offset', 0)
    limit = args.get('limit', 25)
    endpoints_filter = {}
    endpoints_filter.update({'status': status}) if status else None
    endpoints_filter.update({'mac_address': mac_address}) if mac_address else None
    params = {'filter': endpoints_filter, 'offset': offset, 'limit': limit}

    res = client.request_endpoints(method='GET', params=params, url_suffix='endpoint')

    readable_output, outputs = parse_endpoints_response(res)
    human_readable = tableToMarkdown('HPE Aruba Clearpass endpoints', readable_output, removeNull=True)

    return CommandResults(
        readable_output=human_readable,
        outputs_prefix='HPEArubaClearpass.endpoints',
        outputs_key_field='id',
        outputs=outputs,
    )


def update_endpoint_command(client: Client, args: Dict[str, Any]) -> CommandResults:
    endpoint_id = args.get('endpoint_id')
    mac_address = args.get('mac_address')
    status = args.get('status')
    description = args.get('description')
    device_insight_tags = args.get('device_insight_tags')
    attributes = args.get('attributes')

    request_body = {}
    request_body.update({'status': status}) if status else None
    request_body.update({'mac_address': mac_address}) if mac_address else None
    request_body.update({'description': description}) if description else None
    request_body.update({'device_insight_tags': device_insight_tags}) if device_insight_tags else None
    request_body.update({'attributes': attributes}) if attributes else None

    params = {'body': request_body}
    res = client.request_endpoints(method='PATCH', params={}, url_suffix=f'endpoint/{endpoint_id}', body=params)

    outputs = {
        'ID': res.get('id'),
        'MAC Address': res.get('mac_address'),
        'Status': res.get('status', ""),
        'Attributes': res.get('attributes', ""),
        'Description': res.get('description', ""),
        'Device insight tags': res.get('device_insight_tags', "")
    }
    human_readable = tableToMarkdown('HPE Aruba Clearpass endpoints', outputs, removeNull=True)

    return CommandResults(
        readable_output=human_readable,
        outputs_prefix='HPEArubaClearpass.endpoints',
        outputs_key_field='id',
        outputs=outputs,
    )


def main() -> None:
    params = demisto.params()
    base_url = urljoin(params.get('url'), '/api')
    client_id = params.get('client_id')
    client_secret = params.get('client_secret')

    verify_certificate = not params.get('insecure', False)
    proxy = params.get('proxy', False)

    client = Client(proxy=proxy,
                    verify=verify_certificate,
                    base_url=base_url,
                    client_id=client_id,
                    client_secret=client_secret)

    demisto.debug(f'Command being called is {demisto.command()}')
    try:
        client.login()

        if demisto.command() == 'test-module':
            return_results(test_module(client))

        elif demisto.command() == 'aruba-clearpass-endpoints-list':
            return_results(get_endpoints_list_command(client, demisto.args()))

        elif demisto.command() == 'aruba-clearpass-endpoint-update':
            return_results(update_endpoint_command(client, demisto.args()))

    # Log exceptions and return errors
    except Exception as e:
        demisto.error(traceback.format_exc())  # print the traceback
        return_error(f'Failed to execute {demisto.command()} command.\nError:\n{str(e)}')


if __name__ in ('__main__', '__builtin__', 'builtins'):
    main()