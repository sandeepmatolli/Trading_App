# market_data/groww_auth.py

from growwapi import GrowwAPI
from config import GROWW_API_KEY, GROWW_API_SECRET

def get_groww_api():
    """
    Authenticate with Groww API and return a GrowwAPI instance.
    """
    # Retrieve short-lived access token (valid about 1 day)
    access_token = GrowwAPI.get_access_token(api_key=GROWW_API_KEY, secret=GROWW_API_SECRET)
    groww = GrowwAPI(access_token)
    print("Groww API authenticated.")
    return groww

if __name__ == "__main__":
    groww = get_groww_api()
