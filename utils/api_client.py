import os
import requests
import time
import random
from utils.logging_config import get_logger

logger = get_logger("api_client")

def make_api_call(
    url,
    method="GET",
    params=None,
    headers=None,
    data=None,
    retries=3,
    delay=2,
    timeout=10,
    adaptive=True,
    max_delay=120
):
    """
    Makes an API call with retries and adaptive rate limit handling.
    
    Features for overnight sync optimization:
    - Adaptive exponential backoff for rate limits
    - Respects Retry-After header
    - Adds jitter to prevent thundering herd
    - Learns from consecutive rate limit failures
    - Caps maximum wait time to prevent excessive delays
    
    Args:
        adaptive (bool): Enable adaptive backoff (recommended for overnight jobs)
        max_delay (int): Maximum wait time in seconds (default: 120)
    """
    consecutive_rate_limits = 0
    
    for attempt in range(1, retries + 1):
        try:
            if method.upper() == "GET":
                response = requests.get(url, params=params, headers=headers, timeout=timeout)
            elif method.upper() == "POST":
                response = requests.post(url, headers=headers, data=data, timeout=timeout)
            else:
                raise ValueError("Unsupported HTTP method.")

            response.raise_for_status()
            logger.info(f"Attempt {attempt}: API call to {url} succeeded.")
            
            # Success! Reset consecutive rate limit counter
            consecutive_rate_limits = 0
            return response.json()
        
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [429, 418]:
                consecutive_rate_limits += 1
                
                # Check for Retry-After header (respect server's request)
                retry_after = e.response.headers.get("Retry-After")
                
                if retry_after:
                    # Server told us exactly when to retry
                    wait_time = int(retry_after)
                elif adaptive:
                    # Adaptive backoff: increase delay with consecutive failures
                    # Formula: delay * (2 ^ consecutive_failures) * jitter
                    # Jitter prevents all clients retrying at same time
                    jitter = random.uniform(0.8, 1.2)
                    exponential_delay = delay * (2 ** (consecutive_rate_limits - 1))
                    wait_time = min(exponential_delay * jitter, max_delay)
                else:
                    # Simple linear backoff
                    wait_time = delay * attempt
                
                logger.warning(
                    f"Rate limit exceeded ({e.response.status_code}). "
                    f"Consecutive: {consecutive_rate_limits}. "
                    f"Waiting {wait_time:.1f}s before retry {attempt}/{retries}"
                )
                time.sleep(wait_time)
            else:
                logger.warning(f"Attempt {attempt}: HTTP Error calling {url}: {e}")
                if attempt < retries:
                    time.sleep(delay * attempt)
                else:
                    logger.error(f"Failed to call {url} after {retries} attempts due to HTTP error.")
                    
        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt}: Error calling {url}: {e}")
            if attempt < retries:
                time.sleep(delay * attempt)
            else:
                logger.error(f"Failed to call {url} after {retries} attempts.")
                
    return {}

def extract_json_key(data, keys, default=None):
    """
    Extracts a value from a nested JSON response using a list of keys.

    Args:
        data (dict): The JSON response.
        keys (list): List of keys to traverse the nested structure.
        default: Default value to return if the key path does not exist.

    Returns:
        The value found at the key path, or the default value if not found.
    """
    try:
        for key in keys:
            data = data[key]
        return data
    except (KeyError, TypeError):
        logger.warning(f"Key path {keys} not found in JSON response.")
        return default