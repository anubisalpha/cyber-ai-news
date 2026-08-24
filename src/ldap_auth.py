"""LDAP / Active Directory authentication."""
from __future__ import annotations

import ldap3
from ldap3.core.exceptions import LDAPException


def verify_ldap(cfg: dict, username: str, password: str) -> tuple[str | None, bool, str | None]:
    """Attempt LDAP bind for the given username/password.

    Returns (user_dn, is_admin, email) on success, (None, False, None) on failure.

    login_group (stored as user_filter): user must be a member to log in at all.
    group_dn: membership grants admin rights.
    """
    server_host = cfg.get("server", "").strip()
    port = int(cfg.get("port") or 389)
    use_tls = bool(cfg.get("use_tls"))
    base_dn = cfg.get("base_dn", "").strip()
    bind_dn = cfg.get("bind_dn", "").strip()
    bind_password = cfg.get("bind_password", "").strip()
    login_group = (cfg.get("user_filter") or "").strip()   # group DN — must be member to log in
    admin_group = (cfg.get("group_dn") or "").strip()      # group DN — members get admin rights

    if not server_host or not base_dn:
        return None, False, None

    try:
        tls = ldap3.Tls() if use_tls else None
        server = ldap3.Server(server_host, port=port, use_ssl=use_tls, tls=tls,
                              get_info=ldap3.NONE, connect_timeout=5)

        # Service-account bind to find the user's DN
        conn = ldap3.Connection(server, user=bind_dn or None, password=bind_password or None,
                                auto_bind=True, raise_exceptions=True)
        user_filter = f"(sAMAccountName={_escape(username)})"
        conn.search(base_dn, user_filter, attributes=["distinguishedName", "mail"])
        if not conn.entries:
            conn.unbind()
            return None, False, None

        user_dn = str(conn.entries[0].distinguishedName)
        mail_attr = conn.entries[0].mail
        email = str(mail_attr) if mail_attr and mail_attr.value else None

        # Login group check — if configured, user must be a member
        if login_group:
            conn.search(
                login_group,
                f"(member={_escape_dn(user_dn)})",
                search_scope=ldap3.BASE,
                attributes=["cn"],
            )
            if not conn.entries:
                conn.unbind()
                return None, False, None

        conn.unbind()

        # Bind as the user to verify their password
        user_conn = ldap3.Connection(server, user=user_dn, password=password,
                                     auto_bind=True, raise_exceptions=True)

        # Admin group check
        is_admin = False
        if admin_group:
            user_conn.search(
                admin_group,
                f"(member={_escape_dn(user_dn)})",
                search_scope=ldap3.BASE,
                attributes=["cn"],
            )
            is_admin = bool(user_conn.entries)

        user_conn.unbind()
        return user_dn, is_admin, email

    except (LDAPException, Exception):  # noqa: BLE001
        return None, False, None


def _escape(value: str) -> str:
    """Escape special characters in an LDAP filter value (RFC 4515)."""
    return (value
            .replace("\\", "\\5c").replace("*", "\\2a")
            .replace("(", "\\28").replace(")", "\\29")
            .replace("\x00", "\\00"))


def _escape_dn(dn: str) -> str:
    return dn.replace("\\", "\\5c").replace("*", "\\2a").replace("(", "\\28").replace(")", "\\29")
