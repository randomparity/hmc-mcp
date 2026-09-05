"""Shared HMC user vocabulary for clients and presentation adapters."""

from typing import Literal, get_args

AuthenticationFilter = Literal["local", "ldap", "kerberos", "all"]
AUTHENTICATION_TYPES = {"local": "Local", "ldap": "LDAP", "kerberos": "Kerberos"}
VALID_AUTHENTICATION_FILTERS = frozenset(get_args(AuthenticationFilter))
