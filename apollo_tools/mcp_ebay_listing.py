from __future__ import annotations

from typing import Any, Dict

from common.mcp import register


def _ebay_listing(payload: Dict[str, Any]) -> Dict[str, Any]:
    from Apollo.apollo_tools.ebay_listing import run_tool

    return run_tool(dict(payload or {}))


register(
    "apollo.ebay_listing",
    _ebay_listing,
    {
        "name": "apollo.ebay_listing",
        "title": "Apollo eBay Listing",
        "summary": "Draft-first eBay listing workflow with optional gated Inventory API mutation and Aegis VM status/probe hooks.",
        "category": "commerce",
        "readonly": False,
        "target_scope": "external",
        "recommended_for_primary": False,
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "draft",
                        "credential_status",
                        "approve_draft",
                        "approve_approval_draft",
                        "publish_draft",
                        "publish_approval_draft",
                        "setup_status",
                        "seller_prerequisites",
                        "category_suggestions",
                        "sandbox_pilot",
                        "approval_draft",
                        "append_images",
                        "append_draft_images",
                        "prepared_review_packet",
                        "draft_repeats",
                        "draft_repeat_summary",
                        "repeat_summary",
                        "ebay_draft_stats",
                        "vm_status",
                        "vm_probe",
                        "create_or_replace_inventory_item",
                        "create_offer",
                        "publish_offer",
                        "full_listing_flow",
                    ],
                },
                "sku": {"type": "string"},
                "draft_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "category_id": {"type": "string"},
                "price": {"type": ["string", "number", "object"]},
                "quantity": {"type": "integer"},
                "image_urls": {"type": "array", "items": {"type": "string"}},
                "source_image_paths": {"type": "array", "items": {"type": "string"}},
                "source_attachment_urls": {"type": "array", "items": {"type": "string"}},
                "operator_confirmed": {"type": "boolean"},
                "confirm_phrase": {"type": "string"},
            },
        },
    },
)
