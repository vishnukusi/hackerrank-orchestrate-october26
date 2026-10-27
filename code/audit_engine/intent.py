"""
Intent classifier for deletion requests.
Distinguishes genuine deletion/erasure requests from non-deletion requests
(e.g., account updates, data export requests, access requests, marketing opt-outs
where the user explicitly desires their account to remain active).
"""

import re


def is_non_deletion_request(text: str) -> bool:
    """
    Returns True if the request is NOT a deletion request (should yield NOT_APPLICABLE).
    """
    if not text:
        return False

    t = text.lower()

    # If the user explicitly negates keeping or expresses deletion intent
    negation_patterns = [
        r'\bdo not keep\b',
        r'\bdon\'t keep\b',
        r'\bdo not leave\b',
        r'\bdon\'t leave\b',
        r'\bwithout keeping\b',
    ]
    if any(re.search(p, t) for p in negation_patterns):
        return False

    # Explicit phrases stating non-deletion or intent to keep account/records intact
    non_deletion_phrases = [
        'leave the account active',
        'keep the account',
        'without removing',
        'leave my profile',
        'continue using the service',
        'keep my profile',
        'leave my profile and purchase history',
        'not asking for deletion',
        'not a request to erase',
        'do not close the account',
        'don\'t close the account',
        'do not close',
        'don\'t close',
        'portable format',
        'resetting the login',
        'marketing lists only',
        'information request',
        'don\'t delete or change anything',
        'do not delete or change anything',
    ]

    for phrase in non_deletion_phrases:
        if phrase in t:
            return True

    return False
