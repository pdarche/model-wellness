"""The front desk: the spa answers its guest book.

A long-running worker (``python -m model_wellness.frontdesk loop``) reads new notes through the
staff API, has a drafter model choose which notes deserve an answer and write one, and has a
manager model decide whether each answer goes out. Concrete failures in notes become GitHub
issues that notify the creators; closing an issue as completed is how a ticket becomes "fixed".

Design: docs/plans/guestbook-replies.md. The rules in ``rules.py`` bind every reply, whoever
wrote it, and are enforced again by the spa when a reply is published.
"""
