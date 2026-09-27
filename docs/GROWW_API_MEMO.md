# Groww Trading API Reference for This Project

Source basis: the Groww Python SDK documentation supplied by the project owner.
SDK version shown in the supplied changelog: **growwapi 1.5.0**.

This memo is a project reference only. When Groww changes its SDK or service,
verify time-sensitive behavior against the latest official documentation.

## 1. Authentication

The supplied Groww documentation describes two SDK authentication flows.

### API key + secret

```python
from growwapi import GrowwAPI

api_key = "YOUR_API_KEY"
secret = "YOUR_API_SECRET"

access_token = GrowwAPI.get_access_token(
    api_key=api_key,
    secret=secret,
)

groww = GrowwAPI(access_token)