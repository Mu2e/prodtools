"""The UNIX account this process acts as.

The single place prodtools asks "who am I". Every account-derived path
(ledger, code cache, run receipts), the queue owner and the jobsub
submitter resolve through it, so they cannot name different accounts.

Read from the effective uid, never from $USER/$LOGNAME (which is what
getpass.getuser() reads first). `ksu mu2epro` keeps the caller's
environment, so the environment named the caller while the process
wrote as mu2epro: on 2026-09-30 a `ksu mu2epro ... json2jobdef --prod
--enqueue` pushed its cnf to production SAM and then tried to register
the campaign in the caller's ledger, which mu2epro cannot write.
"""
import os
import pwd


def current_account():
    """Login name of the effective uid."""
    return pwd.getpwuid(os.geteuid()).pw_name
