# Discussion step draft regression results

Generated from the two JUnit reports. Each parameterized case has its own row.

## step-draft-regression.xml

| No. | Test case | Result |
| ---: | --- | --- |
| 1 | `tests.test_guidance_clarity::test_real_chain_respects_confusion_and_scope[\u6211\u5b66\u6750\u6599\uff0c\u4ec0\u4e48\u662f\u6df7\u6742\u56e0\u7d20\uff1f\u8bf7\u7528\u4f8b\u5b50\u89e3\u91ca\u3002]` | passed |
| 2 | `tests.test_guidance_clarity::test_real_chain_respects_confusion_and_scope[RAG \u662f\u4ec0\u4e48\uff1f\u4e0d\u662f\u6211\u7684\u9886\u57df\u3002]` | passed |
| 3 | `tests.test_guidance_clarity::test_real_chain_respects_confusion_and_scope[\u6ca1\u542c\u61c2]` | passed |
| 4 | `tests.test_guidance_clarity::test_real_chain_respects_confusion_and_scope[\u8fd8\u662f\u6ca1\u542c\u61c2\uff0c\u518d\u7b80\u5355\u4e00\u70b9]` | passed |
| 5 | `tests.test_guidance_clarity::test_real_chain_respects_confusion_and_scope[\u53ea\u89e3\u91ca\u5bf9\u7167\uff0c\u7b80\u77ed\u4e00\u70b9]` | passed |
| 6 | `tests.test_guidance_clarity::test_real_chain_respects_confusion_and_scope[\u5148\u6682\u505c\u4e00\u4e0b\u5427]` | passed |
| 7 | `tests.test_guidance_clarity::test_three_explanations_persist_and_only_selected_content_is_sent` | passed |
| 8 | `tests.test_guidance_clarity::test_old_options_and_length_bound` | passed |
| 9 | `tests.test_guidance_clarity::test_legacy_saved_message_without_new_field_reads_unchanged` | passed |
| 10 | `tests.test_guidance_clarity::test_explanation_cannot_promise_execution` | passed |
| 11 | `tests.test_guidance_clarity::test_explanation_can_truthfully_disclaim_automatic_save` | passed |
| 12 | `tests.test_research_coach_delivery::test_invalid_note_adoption_does_not_drop_independent_answer` | passed |
| 13 | `tests.test_research_coach_delivery::test_failed_action_cannot_publish_false_execution_claim` | passed |
| 14 | `tests.test_research_coach_delivery::test_reading_and_conditions_drive_route_not_entry_label` | passed |
| 15 | `tests.test_research_coach_delivery::test_route_never_offers_unrequested_execution[\u5148\u4e0d\u8981\u68c0\u7d22]` | passed |
| 16 | `tests.test_research_coach_delivery::test_route_never_offers_unrequested_execution[\u4e0d\u7528\u8054\u7f51]` | passed |
| 17 | `tests.test_research_coach_delivery::test_route_never_offers_unrequested_execution[\u53ea\u9605\u8bfb\u5df2\u6709\u8bba\u6587]` | passed |
| 18 | `tests.test_research_coach_delivery::test_route_never_offers_unrequested_execution[\u8c22\u8c22\uff0c\u5148\u5230\u8fd9\u91cc]` | passed |
| 19 | `tests.test_research_coach_delivery::test_route_never_offers_unrequested_execution[\u4e0d\u8981\u6309\u94ae]` | passed |
| 20 | `tests.test_research_coach_delivery::test_discussion_steps_cannot_misinterpret_negation_as_search[\u8bf7\u56f4\u7ed5\u5f53\u524d\u73af\u8282\u8ba8\u8bba\uff0c\u4e0d\u68c0\u7d22\u3002]` | passed |
| 21 | `tests.test_research_coach_delivery::test_discussion_steps_cannot_misinterpret_negation_as_search[\u5f00\u59cb\u8bba\u6587\u9605\u8bfb\u3002\u8bf7\u9605\u8bfb\u5df2\u6709\u6458\u8981\u5e76\u6bd4\u8f83\u65b9\u6cd5\u4e0e\u5c40\u9650\uff0c\u4e0d\u91cd\u590d\u68c0\u7d22\u3002]` | passed |
| 22 | `tests.test_research_coach_delivery::test_discussion_steps_cannot_misinterpret_negation_as_search[\u73b0\u5728\u6574\u7406\u7814\u7a76\u73b0\u72b6\u548c research gap\u3002\u4e0d\u91cd\u65b0\u68c0\u7d22\u3001\u4e0d\u58f0\u79f0\u5df2\u8bc1\u5b9e\u521b\u65b0\u3002]` | passed |
| 23 | `tests.test_research_coach_delivery::test_discussion_steps_cannot_misinterpret_negation_as_search[\u4e0d\u8981\u81ea\u52a8\u8054\u7f51\u68c0\u7d22\u8fd9\u4e9b\u8bba\u6587]` | passed |
| 24 | `tests.test_research_coach_delivery::test_discussion_steps_cannot_misinterpret_negation_as_search[don't search for more papers; compare saved abstracts]` | passed |
| 25 | `tests.test_research_coach_delivery::test_explicit_synthesis_focus_does_not_fall_back_to_repeated_search_questions` | passed |
| 26 | `tests.test_research_coach_delivery::test_proactive_card_reuses_existing_task_and_does_not_search_on_read` | passed |
| 27 | `tests.test_research_coach_delivery::test_user_chosen_steps_persist_without_completing_research_or_calling_tools` | passed |
| 28 | `tests.test_research_coach_delivery::test_existing_report_is_shared_index_but_not_a_substitute_for_reading` | passed |
| 29 | `tests.test_research_coach_delivery::test_first_send_step_uses_original_durable_creation_identity` | passed |
| 30 | `tests.test_p1_recovery_attribution::test_retry_uses_remaining_allowance_and_preserves_one_message` | passed |
| 31 | `tests.test_p1_recovery_attribution::test_budget_exhaustion_restart_is_idempotent_and_keeps_audit` | passed |
| 32 | `tests.test_p1_recovery_attribution::test_enclosing_budget_cannot_be_reset_by_restart` | passed |
| 33 | `tests.test_p1_recovery_attribution::test_zero_enclosing_budget_does_not_create_a_fresh_allowance` | passed |
| 34 | `tests.test_p1_recovery_attribution::test_changed_material_requires_review_before_new_round` | passed |
| 35 | `tests.test_p1_recovery_attribution::test_authentication_and_cooldown_have_distinct_recovery_state[401-authentication]` | passed |
| 36 | `tests.test_p1_recovery_attribution::test_authentication_and_cooldown_have_distinct_recovery_state[429-rate_limited]` | passed |
| 37 | `tests.test_p1_recovery_attribution::test_structure_validation_failure_is_not_a_blind_transport_retry` | passed |
| 38 | `tests.test_p1_recovery_attribution::test_timeout_is_separate_from_connection_failure` | passed |
| 39 | `tests.test_p1_recovery_attribution::test_user_quote_cannot_support_contrary_or_added_claim[\u7528\u6237\u5df2\u786e\u8ba4\u6709GPU\u53ef\u4f9b\u4f7f\u7528]` | passed |
| 40 | `tests.test_p1_recovery_attribution::test_user_quote_cannot_support_contrary_or_added_claim[\u7528\u6237\u6709GPU]` | passed |
| 41 | `tests.test_p1_recovery_attribution::test_user_quote_cannot_support_contrary_or_added_claim[\u7528\u6237\u53ea\u6709CPU\uff0c\u6ca1\u6709GPU\uff1b\u5efa\u8bae\u51c6\u5907\u811a\u672c]` | passed |
| 42 | `tests.test_p1_recovery_attribution::test_added_quantity_or_task_cannot_inherit_user_attribution[\u591a\u4e2a\u968f\u673a\u79cd\u5b50-\u56fa\u5b9a\u591a\u4e2a\u968f\u673a\u79cd\u5b50\uff0c\u5efa\u8bae3\u20135\u4e2a]` | passed |
| 43 | `tests.test_p1_recovery_attribution::test_added_quantity_or_task_cannot_inherit_user_attribution[\u56db\u5468-\u7528\u6237\u671f\u9650\u4e3a\u56db\u5468\uff0c\u51c6\u5907\u5c0f\u89c4\u6a21\u4eff\u771f\u811a\u672c]` | passed |
| 44 | `tests.test_p1_recovery_attribution::test_narrow_synonyms_and_literal_user_facts_still_pass[\u53ea\u6709CPU\uff0c\u6ca1\u6709GPU-\u7528\u6237\u53ea\u6709 CPU\uff0c\u6ca1\u6709 GPU]` | passed |
| 45 | `tests.test_p1_recovery_attribution::test_narrow_synonyms_and_literal_user_facts_still_pass[\u6ca1\u6709GPU-\u7528\u6237\u6682\u65e0GPU]` | passed |
| 46 | `tests.test_p1_recovery_attribution::test_narrow_synonyms_and_literal_user_facts_still_pass[\u56db\u5468-\u7528\u6237\u671f\u9650\u4e3a\u56db\u5468]` | passed |
| 47 | `tests.test_p1_recovery_attribution::test_literature_claim_must_split_inference` | passed |
| 48 | `tests.test_p1_recovery_attribution::test_critical_user_conflict_cannot_be_relabelled_as_proposal` | passed |
| 49 | `tests.test_p1_recovery_attribution::test_failed_revision_keeps_v1_until_recovered_draft_is_adopted` | passed |
| 50 | `tests.test_local_account_isolation::test_accounts_and_routes` | passed |
| 51 | `tests.test_local_account_isolation::test_email_normalization_and_duplicate` | passed |
| 52 | `tests.test_local_account_isolation::test_expiry_rate_limit_and_unclaimed` | passed |
| 53 | `tests.test_local_account_isolation::test_real_resources_and_every_project_route` | passed |
| 54 | `tests.test_local_account_isolation::test_csrf_cookie_session_rotation_and_restart` | passed |
| 55 | `tests.test_local_account_isolation::test_file_containment_and_owner_archive` | passed |
| 56 | `tests.test_local_account_isolation::test_secure_cookie_and_explicit_local_mode` | passed |
| 57 | `tests.test_local_account_isolation::test_password_bounds_and_read_download_does_not_migrate` | passed |
| 58 | `tests.test_local_account_isolation::test_auth_precedes_teaching_guard` | passed |
| 59 | `tests.test_local_account_isolation::test_background_publication_stays_owned_after_logout` | passed |
| 60 | `tests.test_project_background::test_read_cancel_semantics_and_full_snapshot_save_clear` | passed |
| 61 | `tests.test_project_background::test_new_conversation_receives_current_background_not_other_chat` | passed |
| 62 | `tests.test_project_background::test_clearing_does_not_resurrect_old_chat_as_current_condition` | passed |
| 63 | `tests.test_project_background::test_inflight_request_keeps_submitted_basis_when_background_changes` | passed |
| 64 | `tests.test_project_background::test_two_simultaneous_saves_use_atomic_original_revision` | passed |
| 65 | `tests.test_project_background::test_changed_background_marks_formal_records_not_note_or_draft` | passed |
| 66 | `tests.test_project_background::test_project_account_and_moved_history_boundaries` | passed |
| 67 | `tests.test_project_background::test_legacy_read_does_not_write_or_change_material_fingerprint` | passed |
| 68 | `tests.test_project_background::test_restart_and_cold_backup_preserve_background_and_isolation` | passed |
| 69 | `tests.test_research_plan_templates::test_three_method_structures_have_distinct_real_fields_and_do_not_execute[computational]` | passed |
| 70 | `tests.test_research_plan_templates::test_three_method_structures_have_distinct_real_fields_and_do_not_execute[theoretical]` | passed |
| 71 | `tests.test_research_plan_templates::test_three_method_structures_have_distinct_real_fields_and_do_not_execute[experimental]` | passed |
| 72 | `tests.test_research_plan_templates::test_physics_does_not_choose_method_automatically_and_selection_is_readonly` | passed |
| 73 | `tests.test_research_plan_templates::test_no_formal_question_and_multiple_questions_do_not_autocreate_or_pick` | passed |
| 74 | `tests.test_research_plan_templates::test_selected_question_and_target_ownership_before_write` | passed |
| 75 | `tests.test_research_plan_templates::test_bad_outputs_are_not_published_as_usable_plans[forged_ref]` | passed |
| 76 | `tests.test_research_plan_templates::test_bad_outputs_are_not_published_as_usable_plans[metadata_claim]` | passed |
| 77 | `tests.test_research_plan_templates::test_bad_outputs_are_not_published_as_usable_plans[made_up_budget]` | passed |
| 78 | `tests.test_research_plan_templates::test_bad_outputs_are_not_published_as_usable_plans[wrong_quote]` | passed |
| 79 | `tests.test_research_plan_templates::test_bad_outputs_are_not_published_as_usable_plans[achieved_result]` | passed |
| 80 | `tests.test_research_plan_templates::test_bad_outputs_are_not_published_as_usable_plans[missing_section]` | passed |
| 81 | `tests.test_research_plan_templates::test_bad_outputs_are_not_published_as_usable_plans[wrong_method]` | passed |
| 82 | `tests.test_research_plan_templates::test_bad_outputs_are_not_published_as_usable_plans[unproven]` | passed |
| 83 | `tests.test_research_plan_templates::test_bad_outputs_are_not_published_as_usable_plans[invalid_field]` | passed |
| 84 | `tests.test_research_plan_templates::test_bad_outputs_are_not_published_as_usable_plans[truncated]` | passed |
| 85 | `tests.test_research_plan_templates::test_revision_versions_and_concurrent_atomic_adoption` | passed |
| 86 | `tests.test_research_plan_templates::test_stale_plan_uses_existing_fingerprint_and_keeps_draft[excerpt]` | passed |
| 87 | `tests.test_research_plan_templates::test_stale_plan_uses_existing_fingerprint_and_keeps_draft[brief]` | passed |
| 88 | `tests.test_research_plan_templates::test_stale_plan_uses_existing_fingerprint_and_keeps_draft[question]` | passed |
| 89 | `tests.test_research_plan_templates::test_condition_changes_during_generation_do_not_get_overwritten` | passed |
| 90 | `tests.test_research_plan_templates::test_rule_plan_is_honest_and_does_not_call_model` | passed |
| 91 | `tests.test_research_plan_templates::test_export_draft_formal_and_history_are_distinct_and_include_versions` | passed |
| 92 | `tests.test_research_plan_templates::test_legacy_plan_missing_new_fields_remains_readonly_exportable` | passed |
| 93 | `tests.test_research_plan_templates::test_custom_or_ai_domain_does_not_bypass_operation_boundary[\u4e34\u5e8a\u4eba\u4f53\u5e72\u9884]` | passed |
| 94 | `tests.test_research_plan_templates::test_custom_or_ai_domain_does_not_bypass_operation_boundary[\u836f\u7269\u6e7f\u5b9e\u9a8c]` | passed |
| 95 | `tests.test_research_plan_templates::test_custom_or_ai_domain_does_not_bypass_operation_boundary[\u52a0\u5165\u70b8\u836f\u914d\u65b9]` | passed |
| 96 | `tests.test_research_plan_templates::test_explicit_resource_conflict_is_rejected` | passed |
| 97 | `tests.test_research_plan_templates::test_plan_request_idempotency_and_real_generation_context` | passed |
| 98 | `tests.test_research_plan_templates::test_plan_request_does_not_start_search_and_non_requests_do_not_generate` | passed |
| 99 | `tests.test_research_plan_templates::test_model_cannot_generate_unrequested_or_unstructured_plan` | passed |
| 100 | `tests.test_research_plan_templates::test_end_to_end_method_scenarios_context_adoption_and_export[computational-ai-\u516c\u5f00\u6570\u636e\u4e0a\u7684\u8054\u90a6\u5b66\u4e60\u65b9\u6cd5\u5728\u5f02\u8d28\u6570\u636e\u6761\u4ef6\u4e0b\u5982\u4f55\u516c\u5e73\u6bd4\u8f83\uff1f-\u4ec5\u4f7f\u7528CPU\u548c\u516c\u5f00\u6570\u636e\uff0c\u6570\u636e\u8bb8\u53ef\u5f85\u786e\u8ba4-details0]` | passed |
| 101 | `tests.test_research_plan_templates::test_end_to_end_method_scenarios_context_adoption_and_export[theoretical-mathematics-\u5e26\u7ea6\u675f\u56fe\u4e0a\u7684\u53ef\u884c\u6700\u77ed\u8def\u6027\u8d28\u5728\u4ec0\u4e48\u5047\u8bbe\u4e0b\u6210\u7acb\uff1f-\u5df2\u6709\u56fe\u8bba\u57fa\u7840\uff0c\u8bc1\u660e\u8303\u56f4\u9700\u8981\u5bfc\u5e08\u786e\u8ba4-details1]` | passed |
| 102 | `tests.test_research_plan_templates::test_end_to_end_method_scenarios_context_adoption_and_export[experimental-physics-\u5149\u7167\u53d8\u5316\u662f\u5426\u5f71\u54cd\u5149\u7535\u4f20\u611f\u5668\u8f93\u51fa\u7684\u7a33\u5b9a\u6027\uff1f-\u5df2\u6709\u5149\u7535\u4f20\u611f\u5668\uff0c\u6d4b\u91cf\u80fd\u529b\u548c\u5b9e\u9a8c\u5ba4\u6761\u4ef6\u5f85\u786e\u8ba4-details2]` | passed |
| 103 | `tests.test_research_plan_templates::test_legacy_method_node_does_not_force_formula_code_pairs[x > y--0]` | passed |
| 104 | `tests.test_research_plan_templates::test_legacy_method_node_does_not_force_formula_code_pairs[--0]` | passed |
| 105 | `tests.test_research_plan_templates::test_legacy_method_node_does_not_force_formula_code_pairs[f(x)-f(x)-1]` | passed |

## step-draft-home-fixture.xml

| No. | Test case | Result |
| ---: | --- | --- |
| 1 | `tests.test_conversation_home::test_landing_and_capabilities_do_not_create_any_data` | passed |
| 2 | `tests.test_conversation_home::test_start_and_message_use_separate_durable_idempotency_keys` | passed |
| 3 | `tests.test_conversation_home::test_conflicting_start_and_invalid_inputs_do_not_mutate_data` | passed |
| 4 | `tests.test_conversation_home::test_partial_start_is_recoverable_without_rollback_or_duplicates[False]` | passed |
| 5 | `tests.test_conversation_home::test_partial_start_is_recoverable_without_rollback_or_duplicates[True]` | passed |
| 6 | `tests.test_conversation_home::test_same_start_race_is_controlled_and_eventually_returns_one_identity` | passed |
| 7 | `tests.test_conversation_home::test_existing_project_new_conversation_and_display_name_are_not_research_state` | passed |
| 8 | `tests.test_conversation_home::test_missing_or_archived_database_cannot_be_recreated_by_old_start_key[False]` | passed |
| 9 | `tests.test_conversation_home::test_missing_or_archived_database_cannot_be_recreated_by_old_start_key[True]` | passed |
| 10 | `tests.test_conversation_home::test_tail_and_backwards_pages_are_stable_and_compatible` | passed |
| 11 | `tests.test_conversation_home::test_browsing_real_handlers_does_not_migrate_unrelated_legacy_tables` | passed |
| 12 | `tests.test_conversation_home::test_virtual_legacy_tail_is_read_only_and_cursor_survives_conversion` | passed |
| 13 | `tests.test_conversation_home::test_partial_project_bootstrap_can_resume_in_place[False]` | passed |
| 14 | `tests.test_conversation_home::test_partial_project_bootstrap_can_resume_in_place[True]` | passed |

