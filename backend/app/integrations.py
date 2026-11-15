"""
Mock integrations for chat and HR reimbursement systems.

Why mock?
- The assignment explicitly forbids real external service calls.
- Mocks let us test the full workflow without network dependencies.
- In production these would be replaced with real API clients
  (e.g. Slack SDK, Tripletex API) while keeping the same interface.

MockChatService:
- Stores notifications in a list (simulates sending a Slack/Teams message).
- Tests and the demo endpoint can read the list to verify messages were sent.
- In production: call Slack's /chat.postMessage API.

MockHRService:
- Generates a fake external claim ID (simulates what a real HR system returns).
- Stores submitted claims and their approval state in memory.
- In production: call Visma or Tripletex API, store the returned claim reference.

Design principle: both services have an interface that doesn't change even if
the implementation changes. This is the Dependency Inversion Principle — services.py
depends on the interface, not on the specific mock.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


class MockChatService:
    """
    Simulates an internal messaging platform (Slack, Teams, etc.).

    Records every notification sent so tests can assert what was notified
    and to whom, without actual network calls.
    """

    def __init__(self) -> None:
        self._notifications: List[Dict[str, Any]] = []

    def notify_volunteers(
        self,
        volunteer_ids: List[str],
        volunteer_names: List[str],
        office_name: str,
        request_id: str,
        message: str,
    ) -> None:
        """
        "Send" a notification to all eligible volunteers.
        In production this would call the chat API once per volunteer,
        or post to a channel with @mentions.
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "type": "VOLUNTEER_NEEDED",
            "office": office_name,
            "request_id": request_id,
            "recipients": list(zip(volunteer_ids, volunteer_names)),
            "message": message,
        }
        self._notifications.append(entry)

    def notify_assignment(
        self,
        volunteer_id: str,
        volunteer_name: str,
        office_name: str,
        request_id: str,
    ) -> None:
        """Notify the assigned volunteer that they accepted a request."""
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "type": "ASSIGNMENT_CONFIRMED",
            "office": office_name,
            "request_id": request_id,
            "volunteer_id": volunteer_id,
            "volunteer_name": volunteer_name,
            "message": (
                f"Hi {volunteer_name}, you have been assigned the snack refill "
                f"for the {office_name} office (request #{request_id}). "
                f"Please purchase snacks and submit your receipt."
            ),
        }
        self._notifications.append(entry)

    def get_notifications(self) -> List[Dict[str, Any]]:
        """Return all recorded notifications (for tests and demo inspection)."""
        return list(self._notifications)

    def clear(self) -> None:
        """Clear all notifications (useful in test teardown)."""
        self._notifications.clear()


class MockHRService:
    """
    Simulates an external HR / expense management system.

    Real systems (Tripletex, Visma, SAP Concur) work similarly:
    1. You POST a claim → they return an external reference ID.
    2. An HR approver reviews and approves/rejects in their UI.
    3. Finance marks it reimbursed once payment is made.

    Here we simulate all of that in memory so we can test the full
    workflow without any external dependencies.
    """

    def __init__(self) -> None:
        # {external_hr_claim_id: claim_data_dict}
        self._claims: Dict[str, Dict[str, Any]] = {}

    def submit_claim(
        self,
        employee_name: str,
        office_name: str,
        amount: str,          # Decimal passed as string to avoid float issues
        currency: str,
        receipt_reference: str,
        description: str,
    ) -> str:
        """
        Submit a reimbursement claim to the HR system.
        Returns the external claim ID (simulates what a real API would return).

        Why str for amount? We pass Decimal but the external system speaks JSON.
        Using str avoids any float conversion at the boundary.
        """
        external_id = "HR-" + str(uuid.uuid4())[:6].upper()
        self._claims[external_id] = {
            "external_id": external_id,
            "employee_name": employee_name,
            "office_name": office_name,
            "amount": amount,
            "currency": currency,
            "receipt_reference": receipt_reference,
            "description": description,
            "status": "SUBMITTED",
            "submitted_at": datetime.utcnow().isoformat(),
            "reviewed_at": None,
            "review_note": None,
        }
        return external_id

    def get_claim(self, external_id: str) -> Optional[Dict[str, Any]]:
        return self._claims.get(external_id)

    def get_all_claims(self) -> List[Dict[str, Any]]:
        return list(self._claims.values())

    def approve(self, external_id: str) -> bool:
        """Simulate HR approving a claim."""
        claim = self._claims.get(external_id)
        if not claim:
            return False
        claim["status"] = "APPROVED"
        claim["reviewed_at"] = datetime.utcnow().isoformat()
        return True

    def reject(self, external_id: str, reason: str) -> bool:
        """Simulate HR rejecting a claim."""
        claim = self._claims.get(external_id)
        if not claim:
            return False
        claim["status"] = "REJECTED"
        claim["reviewed_at"] = datetime.utcnow().isoformat()
        claim["review_note"] = reason
        return True


# Singletons — shared across the process, same as repositories
chat_service = MockChatService()
hr_service = MockHRService()
