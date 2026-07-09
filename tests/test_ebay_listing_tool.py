from __future__ import annotations

from Apollo.apollo_tools import ebay_listing


def _listing_payload():
    return {
        "sku": "apollo-test-001",
        "title": "Apollo Test Listing",
        "description": "A test item prepared by Apollo.",
        "condition": "USED_EXCELLENT",
        "quantity": 1,
        "category_id": "9355",
        "price": "19.99",
        "currency": "USD",
        "merchant_location_key": "default-location",
        "fulfillment_policy_id": "fulfillment-1",
        "payment_policy_id": "payment-1",
        "return_policy_id": "return-1",
        "image_urls": ["https://example.com/item.jpg"],
    }


def test_ebay_listing_draft_builds_inventory_item_and_offer(monkeypatch):
    monkeypatch.delenv("APOLLO_EBAY_ENABLE_MUTATIONS", raising=False)
    payload = _listing_payload()

    result = ebay_listing.run_tool({"action": "draft", **payload})

    assert result["ok"] is True
    assert result["sku"] == "apollo-test-001"
    assert result["ready_to_publish"] is True
    assert result["inventory_item"]["product"]["title"] == "Apollo Test Listing"
    assert result["inventory_item"]["product"]["imageUrls"] == ["https://example.com/item.jpg"]
    assert result["offer"]["format"] == "FIXED_PRICE"
    assert result["offer"]["pricingSummary"]["price"] == {"value": "19.99", "currency": "USD"}
    assert result["offer"]["listingPolicies"]["paymentPolicyId"] == "payment-1"
    assert result["shipping"]["payer"] == "buyer"
    assert result["shipping"]["service"] == "USPS Ground Advantage"
    assert result["shipping"]["type"] == "calculated"
    assert result["shipping"]["package"]["estimated"] is True


def test_ebay_listing_forces_buy_it_now_format():
    payload = {**_listing_payload(), "format": "AUCTION"}

    result = ebay_listing.run_tool({"action": "draft", **payload})

    assert result["ok"] is True
    assert result["offer"]["format"] == "FIXED_PRICE"


def test_ebay_listing_estimates_ceramic_mug_package():
    result = ebay_listing.run_tool(
        {
            "action": "draft",
            "title": "Set of Ceramic Coffee Mugs",
            "description": "Kitchen drinkware.",
            "price": "12.99",
            "category_id": "20695",
            "merchant_location_key": "default-location",
            "fulfillment_policy_id": "fulfillment-1",
            "payment_policy_id": "payment-1",
            "return_policy_id": "return-1",
            "image_urls": ["https://example.com/mugs.jpg"],
        }
    )

    assert result["shipping"]["payer"] == "buyer"
    assert result["shipping"]["service"] == "USPS Ground Advantage"
    assert result["shipping"]["package"]["length"] == 10
    assert result["shipping"]["package"]["width"] == 8
    assert result["shipping"]["package"]["height"] == 6
    assert result["shipping"]["package"]["weight_value"] == 3


def test_ebay_listing_preserves_category_specific_aspects():
    result = ebay_listing.run_tool(
        {
            "action": "draft",
            **_listing_payload(),
            "aspects": {
                "Brand": ["Unbranded"],
                "Type": ["Coffee Mug Set"],
                "Color": ["White"],
            },
        }
    )

    aspects = result["inventory_item"]["product"]["aspects"]
    assert aspects["Brand"] == ["Unbranded"]
    assert aspects["Type"] == ["Coffee Mug Set"]
    assert aspects["Color"] == ["White"]


def test_ebay_listing_draft_reports_publish_blockers(monkeypatch):
    monkeypatch.delenv("APOLLO_EBAY_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("EBAY_ACCESS_TOKEN", raising=False)
    result = ebay_listing.run_tool({"action": "draft", "title": "Missing listing"})

    assert result["ok"] is False
    assert "category_id" in result["missing"]
    assert "image_urls" in result["missing"]
    assert result["ready_to_publish"] is False


def test_ebay_listing_draft_infers_price_from_item_context(monkeypatch):
    monkeypatch.delenv("APOLLO_EBAY_ENABLE_MUTATIONS", raising=False)

    result = ebay_listing.run_tool(
        {
            "action": "draft",
            "title": "Set of Ceramic Coffee Mugs",
            "description": "Kitchen drinkware set with visible item photos.",
            "category_id": "20695",
            "merchant_location_key": "default-location",
            "fulfillment_policy_id": "fulfillment-1",
            "payment_policy_id": "payment-1",
            "return_policy_id": "return-1",
            "image_urls": ["https://example.com/mugs.jpg"],
        }
    )

    assert result["offer"]["pricingSummary"]["price"]["value"] == "12.99"
    assert result["offer"]["pricingSummary"]["priceSource"] == "heuristic_drinkware"
    assert result["offer"]["pricingSummary"]["priceInferred"] is True
    assert "price" not in result["missing"]


def test_ebay_listing_draft_uses_online_comparable_prices(monkeypatch):
    monkeypatch.setenv("APOLLO_EBAY_ACCESS_TOKEN", "token")
    seen = {}

    def _fake_request(method, path, body, cfg, timeout_s=20.0):
        seen["method"] = method
        seen["path"] = path
        return {
            "ok": True,
            "response": {
                "itemSummaries": [
                    {
                        "title": "Used Garmin Watch Black",
                        "price": {"value": "45.00", "currency": "USD"},
                        "itemWebUrl": "https://www.ebay.com/itm/1",
                    },
                    {
                        "title": "Garmin Watch Charger Included",
                        "price": {"value": "55.00", "currency": "USD"},
                        "itemWebUrl": "https://www.ebay.com/itm/2",
                    },
                ]
            },
        }

    monkeypatch.setattr(ebay_listing, "_ebay_request", _fake_request)

    result = ebay_listing.run_tool(
        {
            "action": "draft",
            "title": "Garmin Watch",
            "description": "Used Garmin GPS watch with photos.",
            "online_comps": True,
            "category_id": "31387",
            "merchant_location_key": "default-location",
            "fulfillment_policy_id": "fulfillment-1",
            "payment_policy_id": "payment-1",
            "return_policy_id": "return-1",
            "image_urls": ["https://example.com/watch.jpg"],
        }
    )

    assert result["offer"]["pricingSummary"]["price"]["value"] == "50.00"
    assert result["offer"]["pricingSummary"]["priceSource"] == "comparable_average"
    assert result["pricing_decision"]["comparable_count"] == 2
    assert result["pricing_review"]["comparable_low"] == 45.0
    assert result["online_comparable_lookup"]["query"] == "Garmin Watch"
    assert "/buy/browse/v1/item_summary/search" in seen["path"]


def test_ebay_listing_approval_draft_persists_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_listing, "EBAY_APPROVAL_DRAFT_DIR", tmp_path / "drafts")

    result = ebay_listing.run_tool(
        {
            "action": "approval_draft",
            "draft_id": "sky-task-1",
            "title": "Vintage Camera",
            "description": "Camera from Sky image task.",
            "source_agent": "Sky",
            "source_task_id": "sky-task-1",
            "source_image_paths": [r"C:\tmp\camera.jpg"],
        }
    )

    assert result["ok"] is True
    assert result["status"] == "needs_approval"
    assert result["writes_performed"] is False
    assert result["approval_url"] == "/admin/ebay/drafts/sky-task-1"
    assert (tmp_path / "drafts" / "sky-task-1" / "draft.json").exists()
    assert (tmp_path / "drafts" / "sky-task-1" / "approval.md").exists()

    loaded = ebay_listing.load_approval_draft("sky-task-1")
    assert loaded["ok"] is True
    assert loaded["payload"]["source"]["agent"] == "Sky"
    assert loaded["payload"]["draft"]["inventory_item"]["product"]["title"] == "Vintage Camera"


def test_ebay_listing_delete_approval_draft_removes_local_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_listing, "EBAY_APPROVAL_DRAFT_DIR", tmp_path / "drafts")
    ebay_listing.run_tool(
        {
            "action": "approval_draft",
            "draft_id": "delete-me",
            "title": "Vintage Camera",
            "description": "Camera from Sky image task.",
            "source_agent": "Sky",
            "source_task_id": "sky-task-delete",
            "source_attachment_urls": ["https://example.com/camera.jpg"],
            "image_urls": ["https://example.com/camera.jpg"],
        }
    )

    result = ebay_listing.run_tool({"action": "delete_draft", "draft_id": "delete-me"})

    assert result["ok"] is True
    assert result["status"] == "deleted"
    assert not (tmp_path / "drafts" / "delete-me").exists()


def test_ebay_listing_approval_draft_preserves_listing_images(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_listing, "EBAY_APPROVAL_DRAFT_DIR", tmp_path / "drafts")

    result = ebay_listing.run_tool(
        {
            "action": "approval_draft",
            "draft_id": "sky-task-images",
            "title": "Cable Holder",
            "description": "3D printed cable holder.",
            "source_agent": "Sky",
            "source_task_id": "sky-task-images",
            "source_attachment_urls": ["https://example.com/front.jpg"],
            "image_urls": ["https://example.com/front.jpg"],
        }
    )

    draft = result["draft"]
    assert draft["inventory_item"]["product"]["imageUrls"] == ["https://example.com/front.jpg"]
    assert "image_urls" not in draft["missing"]
    loaded = ebay_listing.load_approval_draft("sky-task-images")
    assert loaded["payload"]["draft"]["inventory_item"]["product"]["imageUrls"] == ["https://example.com/front.jpg"]


def test_ebay_listing_append_images_updates_existing_approval_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_listing, "EBAY_APPROVAL_DRAFT_DIR", tmp_path / "drafts")
    ebay_listing.run_tool(
        {
            "action": "approval_draft",
            "draft_id": "sky-task-images",
            "title": "Cable Holder",
            "description": "3D printed cable holder.",
            "source_agent": "Sky",
            "source_task_id": "sky-task-images",
            "source_attachment_urls": ["https://example.com/front.jpg"],
            "image_urls": ["https://example.com/front.jpg"],
        }
    )

    result = ebay_listing.run_tool(
        {
            "action": "append_images",
            "draft_id": "sky-task-images",
            "source_attachment_urls": ["https://example.com/back.jpg"],
            "source_image_paths": [r"C:\tmp\back.jpg"],
        }
    )

    assert result["ok"] is True
    loaded = ebay_listing.load_approval_draft("sky-task-images")
    product = loaded["payload"]["draft"]["inventory_item"]["product"]
    assert product["imageUrls"] == ["https://example.com/front.jpg", "https://example.com/back.jpg"]
    assert loaded["payload"]["source"]["image_paths"] == [r"C:\tmp\back.jpg"]
    assert loaded["payload"]["source"]["attachment_urls"] == ["https://example.com/front.jpg", "https://example.com/back.jpg"]


def test_ebay_listing_append_images_upgrades_request_title_when_better_image_title_arrives(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_listing, "EBAY_APPROVAL_DRAFT_DIR", tmp_path / "drafts")
    ebay_listing.run_tool(
        {
            "action": "approval_draft",
            "draft_id": "sky-task-title-upgrade",
            "title": "can you list this item on ebay please",
            "description": "Initial request-driven draft.",
            "source_agent": "Sky",
            "source_task_id": "sky-task-title-upgrade",
            "source_attachment_urls": ["https://example.com/front.jpg"],
            "image_urls": ["https://example.com/front.jpg"],
        }
    )

    result = ebay_listing.run_tool(
        {
            "action": "append_images",
            "draft_id": "sky-task-title-upgrade",
            "title": "Black curved charging stand",
            "source_attachment_urls": ["https://example.com/side.jpg"],
            "source_image_paths": [r"C:\tmp\side.jpg"],
        }
    )

    assert result["ok"] is True
    assert result["title_updated"] is True
    loaded = ebay_listing.load_approval_draft("sky-task-title-upgrade")
    assert loaded["payload"]["draft"]["inventory_item"]["product"]["title"] == "Black curved charging stand"
    assert loaded["payload"]["image_updates"][-1]["title_updated"] is True
    assert loaded["payload"]["image_updates"][-1]["previous_title"] == "can you list this item on ebay please"
    assert loaded["payload"]["image_updates"][-1]["new_title"] == "Black curved charging stand"


def test_ebay_listing_append_images_keeps_existing_title_when_incoming_title_is_weaker(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_listing, "EBAY_APPROVAL_DRAFT_DIR", tmp_path / "drafts")
    ebay_listing.run_tool(
        {
            "action": "approval_draft",
            "draft_id": "sky-task-title-stable",
            "title": "Black curved charging stand",
            "description": "Readable product evidence already exists.",
            "source_agent": "Sky",
            "source_task_id": "sky-task-title-stable",
            "source_attachment_urls": ["https://example.com/front.jpg"],
            "image_urls": ["https://example.com/front.jpg"],
        }
    )

    result = ebay_listing.run_tool(
        {
            "action": "append_images",
            "draft_id": "sky-task-title-stable",
            "title": "stand",
            "source_attachment_urls": ["https://example.com/back.jpg"],
            "source_image_paths": [r"C:\tmp\back.jpg"],
        }
    )

    assert result["ok"] is True
    assert result["title_updated"] is False
    loaded = ebay_listing.load_approval_draft("sky-task-title-stable")
    assert loaded["payload"]["draft"]["inventory_item"]["product"]["title"] == "Black curved charging stand"
    assert loaded["payload"]["image_updates"][-1]["title_updated"] is False


def test_ebay_listing_update_text_persists_title_description(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_listing, "EBAY_APPROVAL_DRAFT_DIR", tmp_path / "drafts")
    ebay_listing.run_tool(
        {
            "action": "approval_draft",
            "draft_id": "editable-draft",
            "title": "Old Mug Title",
            "description": "Old description.",
            "source_agent": "Sky",
            "source_task_id": "sky-task-edit",
            "image_urls": ["https://example.com/front.jpg"],
        }
    )

    result = ebay_listing.run_tool(
        {
            "action": "update_draft_text",
            "draft_id": "editable-draft",
            "title": "Vintage Ceramic Coffee Mug",
            "description": "Clean pre-owned ceramic coffee mug. Ships with buyer-paid USPS Ground Advantage.",
            "guidance": "make it clearer",
        }
    )

    assert result["ok"] is True
    loaded = ebay_listing.load_approval_draft("editable-draft")
    product = loaded["payload"]["draft"]["inventory_item"]["product"]
    offer = loaded["payload"]["draft"]["offer"]
    assert product["title"] == "Vintage Ceramic Coffee Mug"
    assert product["description"].startswith("Clean pre-owned")
    assert offer["listingDescription"] == product["description"]
    assert loaded["payload"]["draft"]["shipping"]["payer"] == "buyer"


def test_ebay_listing_update_setup_fields_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_listing, "EBAY_APPROVAL_DRAFT_DIR", tmp_path / "drafts")
    ebay_listing.run_tool(
        {
            "action": "approval_draft",
            "draft_id": "setup-edit-draft",
            "title": "Coffee Mug",
            "description": "Pre-owned mug.",
            "price": "12.99",
            "category_id": "20695",
            "merchant_location_key": "loc-1",
            "fulfillment_policy_id": "fulfillment-1",
            "payment_policy_id": "payment-1",
            "return_policy_id": "return-1",
            "image_urls": ["https://example.com/mug.jpg"],
        }
    )

    result = ebay_listing.run_tool(
        {
            "action": "update_draft_text",
            "draft_id": "setup-edit-draft",
            "price": "14.50",
            "currency": "USD",
            "condition": "USED_GOOD",
            "quantity": "2",
            "category_id": "13905",
            "shipping_payer": "buyer",
            "shipping_service": "USPS Ground Advantage",
            "shipping_type": "calculated",
            "package_length": "9",
            "package_width": "7",
            "package_height": "5",
            "package_unit": "INCH",
            "package_weight_value": "2",
            "package_weight_unit": "POUND",
        }
    )

    assert result["ok"] is True
    loaded = ebay_listing.load_approval_draft("setup-edit-draft")
    draft = loaded["payload"]["draft"]
    offer = draft["offer"]
    inventory = draft["inventory_item"]
    assert offer["pricingSummary"]["price"] == {"value": "14.50", "currency": "USD"}
    assert offer["categoryId"] == "13905"
    assert offer["availableQuantity"] == 2
    assert inventory["condition"] == "USED_GOOD"
    assert inventory["availability"]["shipToLocationAvailability"]["quantity"] == 2
    assert draft["shipping"]["service"] == "USPS Ground Advantage"
    assert draft["shipping"]["package"]["length"] == 9
    assert draft["shipping"]["package"]["weight_value"] == 2


def test_ebay_listing_prepared_review_packet_is_human_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_listing, "EBAY_APPROVAL_DRAFT_DIR", tmp_path / "drafts")

    result = ebay_listing.run_tool(
        {
            "action": "prepared_review_packet",
            **_listing_payload(),
            "draft_id": "human-review-packet",
            "source_image_paths": [r"C:\tmp\front.jpg"],
            "comparable_prices": [
                {"price": "18.50", "source": "sold comp 1", "sold": True},
                {"price": "22.00", "source": "sold comp 2", "sold": True},
            ],
        }
    )

    assert result["ok"] is True
    assert result["writes_performed"] is False
    assert result["ui_automation_allowed"] is True
    assert result["final_publish_allowed"] is False
    assert result["human_review_gate"]["must_stop_before_publish"] is True
    assert result["pricing_review"]["sold_comparable_count"] == 2
    assert result["image_review"]["listing_image_count"] == 1
    assert any(step["field"] == "stop_before_publish" for step in result["forgeclaw_fill_steps"])
    assert (tmp_path / "drafts" / "human-review-packet" / "prepared_review_packet.json").exists()
    md_path = tmp_path / "drafts" / "human-review-packet" / "prepared_review_packet.md"
    assert md_path.exists()
    assert "ForgeClaw must not click final publish/list/submit" in md_path.read_text(encoding="utf-8")


def test_ebay_listing_prepared_review_packet_warns_without_comps(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_listing, "EBAY_APPROVAL_DRAFT_DIR", tmp_path / "drafts")

    result = ebay_listing.run_tool({"action": "prepared_review_packet", **_listing_payload(), "draft_id": "no-comps"})

    assert result["pricing_review"]["comparable_count"] == 0
    assert any("No comparable sale data" in warning for warning in result["pricing_review"]["warnings"])


def test_ebay_listing_mutation_requires_env_and_operator_confirmation(monkeypatch):
    monkeypatch.delenv("APOLLO_EBAY_ENABLE_MUTATIONS", raising=False)

    blocked = ebay_listing.run_tool({"action": "create_offer", **_listing_payload(), "operator_confirmed": True})

    assert blocked["ok"] is False
    assert blocked["error"] == "ebay_mutations_disabled"

    monkeypatch.setenv("APOLLO_EBAY_ENABLE_MUTATIONS", "1")
    blocked = ebay_listing.run_tool({"action": "create_offer", **_listing_payload()})

    assert blocked["ok"] is False
    assert blocked["error"] == "operator_confirmation_required"


def test_ebay_publish_requires_explicit_publish_gate(monkeypatch):
    monkeypatch.setenv("APOLLO_EBAY_ENABLE_MUTATIONS", "1")
    monkeypatch.delenv("APOLLO_EBAY_ENABLE_PUBLISH", raising=False)

    blocked = ebay_listing.run_tool(
        {
            "action": "publish_offer",
            "offer_id": "123",
            "operator_confirmed": True,
            "confirm_phrase": ebay_listing.PUBLISH_CONFIRM_PHRASE,
        }
    )

    assert blocked["ok"] is False
    assert blocked["error"] == "ebay_publish_disabled"


def test_ebay_withdraw_offer_uses_inventory_withdraw_endpoint(monkeypatch):
    monkeypatch.setenv("APOLLO_EBAY_ENABLE_MUTATIONS", "1")
    monkeypatch.setenv("APOLLO_EBAY_ACCESS_TOKEN", "token")
    seen = {}

    def _fake_request(method, path, body, cfg, timeout_s=20.0):
        seen["method"] = method
        seen["path"] = path
        return {"ok": True, "response": {"status": "withdrawn"}}

    monkeypatch.setattr(ebay_listing, "_ebay_request", _fake_request)

    result = ebay_listing.run_tool({"action": "withdraw_offer", "offer_id": "12345", "operator_confirmed": True})

    assert result["ok"] is True
    assert seen["method"] == "POST"
    assert seen["path"] == "/sell/inventory/v1/offer/12345/withdraw"


def test_ebay_listing_vm_probe_uses_aegis_client(monkeypatch):
    monkeypatch.setattr(
        ebay_listing.aegis_vm_client,
        "vm_probe",
        lambda timeout_s=15.0: {"ok": True, "stdout": "apollo-aegis-vm-ok", "timeout_s": timeout_s},
    )

    result = ebay_listing.run_tool({"action": "vm_probe", "timeout_s": 3})

    assert result["ok"] is True
    assert result["stdout"] == "apollo-aegis-vm-ok"
    assert result["timeout_s"] == 3


def test_ebay_listing_setup_status_reports_next_steps(monkeypatch):
    monkeypatch.setenv("APOLLO_EBAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr(
        ebay_listing.aegis_vm_client,
        "vm_status",
        lambda timeout_s=5.0: {"ok": True, "ready": True, "timeout_s": timeout_s},
    )

    result = ebay_listing.run_tool({"action": "setup_status", "include_vm": True, **_listing_payload()})

    assert result["credential_status"]["access_token_present"] is True
    assert result["draft"]["ready_to_publish"] is True
    assert result["vm_status"]["ready"] is True
    assert "Run seller_prerequisites to read available policy IDs and inventory locations." in result["next_steps"]


def test_ebay_listing_seller_prerequisites_reads_policies_and_locations(monkeypatch):
    monkeypatch.setenv("APOLLO_EBAY_ACCESS_TOKEN", "token")
    calls = []

    def _fake_request(method, path, body, cfg, timeout_s=20.0):
        calls.append(path)
        return {"ok": True, "path": path, "response": {"items": []}}

    monkeypatch.setattr(ebay_listing, "_ebay_request", _fake_request)

    result = ebay_listing.run_tool({"action": "seller_prerequisites"})

    assert result["ok"] is True
    assert any("/sell/account/v1/fulfillment_policy" in path for path in calls)
    assert any("/sell/account/v1/payment_policy" in path for path in calls)
    assert any("/sell/account/v1/return_policy" in path for path in calls)
    assert any("/sell/inventory/v1/location" in path for path in calls)


def _fake_policy_request(method, path, body, cfg, timeout_s=20.0):
    if "fulfillment_policy" in path:
        return {
            "ok": True,
            "response": {
                "fulfillmentPolicies": [
                    {
                        "name": "Aegis Buyer Paid Shipping",
                        "fulfillmentPolicyId": "fulfillment-1",
                        "shippingOptions": [
                            {
                                "optionType": "DOMESTIC",
                                "costType": "FLAT_RATE",
                                "shippingServices": [
                                    {
                                        "shippingCarrierCode": "USPS",
                                        "shippingServiceCode": "USPSPriorityFlatRateBox",
                                        "shippingCost": {"value": "7.15", "currency": "USD"},
                                        "freeShipping": False,
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "name": "Ground Advantage Calculated",
                        "fulfillmentPolicyId": "fulfillment-ground",
                        "shippingOptions": [
                            {
                                "optionType": "DOMESTIC",
                                "costType": "CALCULATED",
                                "shippingServices": [
                                    {
                                        "shippingCarrierCode": "USPS",
                                        "shippingServiceCode": "USPSGroundAdvantage",
                                        "freeShipping": False,
                                    }
                                ],
                            }
                        ],
                    },
                ]
            },
        }
    if "payment_policy" in path:
        return {"ok": True, "response": {"paymentPolicies": [{"name": "Default Payment", "paymentPolicyId": "payment-1"}]}}
    if "return_policy" in path:
        return {"ok": True, "response": {"returnPolicies": [{"name": "30 Day Returns", "returnPolicyId": "return-1"}]}}
    if "/sell/inventory/v1/location" in path:
        return {"ok": True, "response": {"locations": [{"name": "Warehouse", "merchantLocationKey": "default-location", "merchantLocationStatus": "ENABLED"}]}}
    return {"ok": True, "response": {}}


def test_ebay_listing_publish_readiness_flags_policy_mismatch(monkeypatch):
    monkeypatch.setenv("APOLLO_EBAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr(ebay_listing, "_ebay_request", _fake_policy_request)

    draft = ebay_listing.run_tool({"action": "draft", **_listing_payload(), "shipping_service": "USPS Ground Advantage", "shipping_type": "calculated"})
    readiness = ebay_listing.run_tool({"action": "publish_readiness", "draft": draft})

    assert readiness["ok"] is True
    assert any("USPS Ground Advantage" in warning and "USPSPriorityFlatRateBox" in warning for warning in readiness["warnings"])
    assert any("flat-rate" in warning for warning in readiness["warnings"])
    assert readiness["policy_options"]["fulfillment"][1]["id"] == "fulfillment-ground"


def test_ebay_listing_approve_stores_publish_readiness(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_EBAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr(ebay_listing, "EBAY_APPROVAL_DRAFT_DIR", tmp_path / "drafts")
    monkeypatch.setattr(ebay_listing, "_ebay_request", _fake_policy_request)
    ebay_listing.run_tool({"action": "approval_draft", "draft_id": "policy-check-draft", **_listing_payload(), "shipping_service": "USPS Ground Advantage", "shipping_type": "calculated"})

    approved = ebay_listing.run_tool({"action": "approve_draft", "draft_id": "policy-check-draft"})

    assert approved["ok"] is True
    loaded = ebay_listing.load_approval_draft("policy-check-draft")
    readiness = loaded["payload"]["publish_readiness"]
    assert readiness["prerequisites_ok"] is True
    assert readiness["policy_options"]["payment"][0]["id"] == "payment-1"
    assert any("selected eBay fulfillment policy uses USPSPriorityFlatRateBox" in warning for warning in readiness["warnings"])


def test_ebay_listing_category_suggestions_uses_taxonomy_endpoint(monkeypatch):
    monkeypatch.setenv("APOLLO_EBAY_ACCESS_TOKEN", "token")
    seen = {}

    def _fake_request(method, path, body, cfg, timeout_s=20.0):
        seen["path"] = path
        return {"ok": True, "response": {"categorySuggestions": []}}

    monkeypatch.setattr(ebay_listing, "_ebay_request", _fake_request)

    result = ebay_listing.run_tool({"action": "category_suggestions", "query": "used garmin watch"})

    assert result["ok"] is True
    assert result["category_tree_id"] == "0"
    assert "/commerce/taxonomy/v1/category_tree/0/get_category_suggestions" in seen["path"]
    assert "used+garmin+watch" in seen["path"]


def test_ebay_listing_sandbox_pilot_is_dry_run(monkeypatch):
    monkeypatch.setenv("APOLLO_EBAY_ACCESS_TOKEN", "token")
    monkeypatch.setattr(
        ebay_listing.aegis_vm_client,
        "vm_status",
        lambda timeout_s=5.0: {"ok": True, "ready": True},
    )
    monkeypatch.setattr(
        ebay_listing,
        "_ebay_request",
        lambda method, path, body, cfg, timeout_s=20.0: {"ok": True, "path": path, "response": {}},
    )

    result = ebay_listing.run_tool({"action": "sandbox_pilot", **_listing_payload()})

    assert result["stage"] == "dry_run_plan"
    assert result["writes_performed"] is False
    assert result["create_sequence"][:2] == ["create_or_replace_inventory_item", "create_offer"]


def test_ebay_oauth_status_reports_missing_client_setup(monkeypatch):
    for key in (
        "APOLLO_EBAY_CLIENT_ID",
        "APOLLO_EBAY_CLIENT_SECRET",
        "APOLLO_EBAY_RUNAME",
        "APOLLO_EBAY_REFRESH_TOKEN",
        "APOLLO_EBAY_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)

    result = ebay_listing.run_tool({"action": "oauth_status"})

    assert result["ok"] is False
    assert result["client_id_present"] is False
    assert result["client_secret_present"] is False
    assert result["redirect_uri_present"] is False
    assert "APOLLO_EBAY_CLIENT_ID" in result["missing_env"]
    assert ebay_listing.DEFAULT_SELLER_SCOPES == result["scopes"]


def test_ebay_oauth_authorize_url_uses_client_and_runame(monkeypatch):
    monkeypatch.setenv("APOLLO_EBAY_CLIENT_ID", "client-123")
    monkeypatch.setenv("APOLLO_EBAY_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv("APOLLO_EBAY_RUNAME", "Alex-Test-RuName")

    result = ebay_listing.run_tool({"action": "oauth_authorize_url", "state": "state-1"})

    assert result["ok"] is True
    assert result["state"] == "state-1"
    assert "client_id=client-123" in result["authorize_url"]
    assert "redirect_uri=Alex-Test-RuName" in result["authorize_url"]
    assert "response_type=code" in result["authorize_url"]
    assert "client_secret" not in result["authorize_url"]


def test_ebay_oauth_exchange_code_stores_tokens_without_returning_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("APOLLO_EBAY_CLIENT_ID", "client-123")
    monkeypatch.setenv("APOLLO_EBAY_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv("APOLLO_EBAY_RUNAME", "Alex-Test-RuName")

    def _fake_post(url, headers, data, timeout):
        class Response:
            status_code = 200
            text = "{}"

            def json(self):
                return {
                    "access_token": "access-secret",
                    "expires_in": 7200,
                    "refresh_token": "refresh-secret",
                    "refresh_token_expires_in": 47304000,
                    "token_type": "User Access Token",
                }

        assert headers["Authorization"].startswith("Basic ")
        assert "grant_type=authorization_code" in data
        assert "code=auth-code" in data
        return Response()

    monkeypatch.setattr(ebay_listing.requests, "post", _fake_post)
    env_file = tmp_path / ".env"
    result = ebay_listing.run_tool({"action": "oauth_exchange_code", "code": "auth-code", "env_file": str(env_file)})

    assert result["ok"] is True
    assert result["token"]["access_token_present"] is True
    assert result["token"]["refresh_token_present"] is True
    assert result["store"]["stored"] is True
    assert result["store"]["stored_keys"] == ["APOLLO_EBAY_ACCESS_TOKEN", "APOLLO_EBAY_REFRESH_TOKEN"]
    assert "access-secret" not in str(result["response_redacted"])
    assert "refresh-secret" not in str(result["response_redacted"])
    assert "APOLLO_EBAY_ACCESS_TOKEN=access-secret" in env_file.read_text(encoding="utf-8")
    assert "APOLLO_EBAY_REFRESH_TOKEN=refresh-secret" in env_file.read_text(encoding="utf-8")


def test_ebay_oauth_refresh_requires_refresh_token(monkeypatch):
    monkeypatch.delenv("APOLLO_EBAY_REFRESH_TOKEN", raising=False)

    result = ebay_listing.run_tool({"action": "oauth_refresh_access_token"})

    assert result["ok"] is False
    assert result["error"] == "refresh_token_required"
