# JARVIS benchmark — 2026-09-21 07:10

Machine: Intel64 Family 6 Model 204 Stepping 3, GenuineIntel · Windows-11-10.0.26200-SP0 · Python 3.12.10 · DPI per-monitor-v2

| metric | n | median ms | min | max |
|---|---:|---:|---:|---:|
| app_launch_verified_ms | 1 | **605** | 605 | 605 |
| native_key_tap_ms | 20 | **0** | 0 | 0 |
| native_mouse_move_ms | 20 | **0** | 0 | 0 |
| native_click_ms | 10 | **53** | 51 | 68 |
| uia_observe_ms | 8 | **101** | 98 | 216 |
| uia_find_control_ms | 8 | **3** | 3 | 4 |
| uia_text_read_ms | 5 | **4** | 4 | 4 |
| type_20chars_paste_verified_ms | 5 | **212** | 175 | 219 |
| verification_state_changed_ms | 5 | **133** | 131 | 134 |
| verification_fingerprint_ms | 8 | **132** | 127 | 136 |
| uia_click_menu_routed_verified_ms | 5 | **1019** | 1012 | 1050 |
| screenshot_window_ms | 8 | **33** | 32 | 53 |
| ocr_window_ms | 3 | **474** | 467 | 766 |
| screenshot_tool_full_ms | 3 | **135** | 129 | 136 |
| mcp_server_startup_ms | 1 | **588** | 588 | 588 |
| mcp_roundtrip_status_ms | 8 | **2** | 1 | 135 |
| mcp_roundtrip_observe_ms | 8 | **102** | 100 | 160 |
| whisper_load_ms | 1 | **4245** | 4245 | 4245 |
| whisper_base.en_3.9s_audio_ms | 3 | **1244** | 1190 | 1675 |
| claude_connect_ms | 1 | **11960** | 11960 | 11960 |
| claude_turn_text_only_ms | 1 | **4121** (first token 3280 ms) | 4121 | 4121 |
| claude_turn_one_tool_ms | 1 | **5849** (first token 5819 ms) | 5849 | 5849 |
| total_launch_observe_type_verify_ms | 3 | **2180** | 2151 | 2695 |
