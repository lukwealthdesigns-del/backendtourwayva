"""Collaboration module - IMPLEMENTED (Phase 5).
See schemas.py and:
  - invitation_service.py (InvitationService: invite/accept/reject/
    cancel, secure token, email-match-gated acceptance, 7-day expiry)
  - comment_service.py (CommentService: trip comments, any member can
    post, only the author or the trip owner can delete)
  - presenters.py (attaches each member's/commenter's/invitee's PUBLIC
    profile — username, name, avatar — to responses in one batched
    query; hides the invitee's email on @username invites)
  - vote_service.py (VoteService: advisory upvote/downvote on
    PendingItineraryChange proposals - does not auto-apply or
    auto-reject; confirm/reject authority stays with editor/owner,
    exactly as Phase 4 built it)

Member management itself (list/change-role/remove) lives on
TripService (app/modules/trips/service.py) rather than here, since it
extends trip-level authorization TripService already owns.

Invitations can be addressed by email OR by @username (exactly one);
the invitee finds them via GET /invitations/me and accepts/rejects with
the token shown there.

NOT yet implemented: inviting a user who doesn't have a Tour-Wayva
account yet and prompting them to sign up on invitation-link click
(the invitation record and email are sent regardless; acceptance
still requires an existing, logged-in account whose email matches)."""
