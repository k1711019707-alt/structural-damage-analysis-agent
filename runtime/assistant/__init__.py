"""Read-only, multi-source assistant runtime for the desktop GUI."""

from runtime.assistant.context import AssistantContextResolver, DetectionRun
from runtime.assistant.service import AssistantService
from runtime.assistant.store import ConversationStore

__all__ = ["AssistantContextResolver", "AssistantService", "ConversationStore", "DetectionRun"]
