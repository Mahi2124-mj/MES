import base64
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import List, Optional


NS = {
    "soap": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}


def build_authenticated_opener(username: str, password: str):
    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, "http://", username, password)
    password_mgr.add_password(None, "https://", username, password)

    handlers = [
        urllib.request.HTTPBasicAuthHandler(password_mgr),
        urllib.request.HTTPDigestAuthHandler(password_mgr),
    ]
    return urllib.request.build_opener(*handlers)


def post_soap_xml(url: str, xml_body: str, username: str, password: str, timeout: int = 8) -> bytes:
    opener = build_authenticated_opener(username, password)
    request = urllib.request.Request(url, data=xml_body.encode("utf-8"), method="POST")
    request.add_header("Content-Type", 'application/soap+xml; charset=utf-8')

    basic_token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request.add_header("Authorization", f"Basic {basic_token}")

    with opener.open(request, timeout=timeout) as response:
        return response.read()


def get_media_service_url(host: str, username: str, password: str) -> Optional[str]:
    device_service_url = f"http://{host}/onvif/device_service"
    envelope = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soap:Envelope xmlns:soap=\"http://www.w3.org/2003/05/soap-envelope\" xmlns:tds=\"http://www.onvif.org/ver10/device/wsdl\">
  <soap:Body>
    <tds:GetCapabilities>
      <tds:Category>Media</tds:Category>
    </tds:GetCapabilities>
  </soap:Body>
</soap:Envelope>"""

    try:
        payload = post_soap_xml(device_service_url, envelope, username, password)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None

    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None

    for tag in [
        ".//tt:Media/XAddr",
        ".//tt:Media/tt:XAddr",
        ".//trt:XAddr",
        ".//XAddr",
    ]:
        node = root.find(tag, NS)
        if node is not None and node.text:
            return node.text.strip()
    return None


def get_onvif_rtsp_uris(host: str, username: str, password: str) -> List[str]:
    media_url = get_media_service_url(host, username, password)
    if not media_url:
        return []

    profiles_envelope = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soap:Envelope xmlns:soap=\"http://www.w3.org/2003/05/soap-envelope\" xmlns:trt=\"http://www.onvif.org/ver10/media/wsdl\">
  <soap:Body>
    <trt:GetProfiles />
  </soap:Body>
</soap:Envelope>"""

    try:
        profiles_payload = post_soap_xml(media_url, profiles_envelope, username, password)
        root = ET.fromstring(profiles_payload)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ET.ParseError):
        return []

    tokens = []
    for profile in root.findall(".//trt:Profiles", NS):
        token = profile.attrib.get("token")
        if token:
            tokens.append(token)
    if not tokens:
        for profile in root.findall(".//Profiles"):
            token = profile.attrib.get("token")
            if token:
                tokens.append(token)

    rtsp_uris: List[str] = []
    for token in tokens:
        stream_uri_envelope = f"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<soap:Envelope xmlns:soap=\"http://www.w3.org/2003/05/soap-envelope\" xmlns:trt=\"http://www.onvif.org/ver10/media/wsdl\" xmlns:tt=\"http://www.onvif.org/ver10/schema\">
  <soap:Body>
    <trt:GetStreamUri>
      <trt:StreamSetup>
        <tt:Stream>RTP-Unicast</tt:Stream>
        <tt:Transport>
          <tt:Protocol>RTSP</tt:Protocol>
        </tt:Transport>
      </trt:StreamSetup>
      <trt:ProfileToken>{token}</trt:ProfileToken>
    </trt:GetStreamUri>
  </soap:Body>
</soap:Envelope>"""

        try:
            uri_payload = post_soap_xml(media_url, stream_uri_envelope, username, password)
            uri_root = ET.fromstring(uri_payload)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ET.ParseError):
            continue

        for tag in [".//tt:Uri", ".//Uri"]:
            node = uri_root.find(tag, NS)
            if node is not None and node.text:
                uri = node.text.strip()
                if uri not in rtsp_uris:
                    rtsp_uris.append(uri)

    return rtsp_uris
