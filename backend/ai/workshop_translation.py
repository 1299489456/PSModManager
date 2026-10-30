"""Project host-fetched Workshop text into the existing translation job input."""
from .gateway import AIError


def translation_source(detail, *, check=lambda: None):
    """Return independent title/description segments; never alter Workshop data.

    ``detail`` must come from WorkshopService.detail, not a page-supplied body.
    Source truncation is carried to the job's final receipt so a translated
    excerpt cannot be presented as the complete Workshop description.
    """
    check()
    if isinstance(detail, dict) and (detail.get('source_pending') or detail.get('description_scope') == 'summary'):
        raise AIError('AI_TRANSLATION_SOURCE_PENDING')
    if (not isinstance(detail, dict) or not isinstance(detail.get('title'), str)
            or not isinstance(detail.get('description'), str)
            or type(detail.get('description_truncated', False)) is not bool):
        raise AIError('AI_TRANSLATION_SOURCE_INVALID')
    if not detail['title'].strip() and not detail['description'].strip():
        raise AIError('AI_TRANSLATION_SOURCE_EMPTY')
    return dict(parts=[('title', detail['title']), ('description', detail['description'])],
                truncated=detail.get('description_truncated', False))
