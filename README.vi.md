# Aleph Skill

**Agent Skill mô phỏng dòng thời gian dựa trên chứng cứ: từ một điểm thay đổi, dựng lại các nhánh quá khứ phản thực, hiện tại thay thế và tương lai xác suất — với cơ chế nhân quả, nguồn chứng cứ và độ bất định luôn được ghi rõ.**

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Release](https://img.shields.io/github/v/release/d-init-d/aleph-skill?sort=semver)](https://github.com/d-init-d/aleph-skill/releases)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-portable-6f42c1)](https://agentskills.io/)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)](https://www.python.org/)
[![Self-test](https://img.shields.io/badge/self--test-npm%20run%20self--test-brightgreen)](README.md#verification)

Aleph Skill biến câu hỏi “nếu như?” thành một mô hình kịch bản có thể kiểm toán: nguồn tạo thành chứng cứ, chứng cứ tạo thành node, node nối thành cạnh nhân quả, rồi các cạnh đó lan truyền thành nhiều timeline xác suất.

Skill được thiết kế cho Codex, OpenCode, Claude Code và các runtime Agent Skills tương thích.

## Tổng quan

| Hạng mục | Aleph Skill cung cấp |
|---|---|
| Mục đích chính | Lịch sử phản thực, can thiệp ở hiện tại, timeline lai quá khứ–hiện tại–tương lai, và mô phỏng hiệu ứng cánh bướm. |
| Mô hình mô phỏng | Node có chứng cứ, cạnh nhân quả có cơ chế, propagation trace, xác suất nhánh và nhãn bất định. |
| Yếu tố con người | Hồ sơ public-role được tách khỏi giả thuyết nhập vai; roleplay không bao giờ được xem là chứng cứ. |
| Độ sâu nghiên cứu | Tự mở rộng theo quy mô sự kiện, địa lý, số tác nhân, độ sâu nhân quả, mức bất định và hệ trọng. |
| Đầu ra | Báo cáo chuyên nghiệp, evidence map, causal graph, branch ledger, propagation trace, validation report và audit metadata. |
| Tính portable | Skill markdown gọn, script ưu tiên Python stdlib, adapter cho nhiều môi trường agent. |
| An toàn | Không tiên tri chắc chắn, không profiling người riêng tư, không doxxing, không vượt access control. |

## Khi nào nên dùng

Dùng Aleph Skill khi agent cần:

- dựng lại baseline trước một điểm thay đổi;
- mô phỏng một thay đổi trong quá khứ sẽ làm hiện tại khác đi như thế nào;
- giả định một can thiệp ở hiện tại rồi dự phóng nhiều nhánh tương lai;
- phân tích kịch bản chính sách, thị trường, địa chính trị, xã hội, khí hậu, công nghệ hoặc tổ chức;
- lần theo hiệu ứng cánh bướm qua chuỗi nhân quả, ngưỡng kích hoạt, feedback loop và độ trễ;
- mô hình hóa quyết định của con người trong vai trò công khai mà không biến suy đoán riêng tư thành sự thật;
- tạo báo cáo tách rõ `fact`, `inference`, `simulation` và `counterfactual`.

Không dùng skill để tuyên bố một tương lai là chắc chắn, profiling người riêng tư, deanonymize, vượt rào truy cập, hoặc thu thập thông tin cá nhân nhạy cảm.

## Phạm vi sản phẩm

Đây là một Agent Skill portable, không phải dịch vụ dự báo, Python package, API server, crawler hay bảng xếp hạng benchmark.

Agent đọc `SKILL.md` làm điểm vào, sau đó chỉ tải các reference và template cần cho kịch bản. Các script đi kèm có chủ đích nhỏ, cục bộ và dễ kiểm tra: khởi tạo workspace, validate artifact, chấm butterfly amplification, render báo cáo và kiểm tra package.

Để có lớp chứng cứ mạnh nhất, nên dùng cùng [D Research](https://github.com/d-init-d/d-research-skill), companion skill cho nghiên cứu bằng trình duyệt và evidence workflow có thể kiểm toán. Aleph Skill giữ vai trò mô phỏng nhân quả; D Research được khuyến nghị chứ không bundle kèm, để skill vẫn portable.

## Vòng đời workflow

| Phase | Việc diễn ra | Artifact chính |
|---|---|---|
| 0. Frame | Xác định điểm thay đổi, observation cutoff, horizon, địa lý, domain và temporal mode. | `simulation-manifest.json` |
| 1. Research | Dựng baseline, source map, evidence map, contradiction notes và uncertainty register. | `evidence-map.csv` |
| 2. Construct | Tạo entity, event, factor, context, indicator, claim, source và actor nodes. | `timeline-node.json`, `actor-dossier.json` |
| 3. Link | Chỉ nhận cạnh nhân quả có cơ chế, độ trễ, bối cảnh, chứng cứ, strength và confidence. | `causal-edge.json` |
| 4. Propagate | Lan truyền tác động qua threshold, feedback loop, amplification path và decay. | `propagation-trace.jsonl` |
| 5. Branch | Tạo nhiều timeline khả dĩ với tổng xác suất bằng 1.0. | `branch-ledger.json` |
| 6. Human decisions | Tách nghiên cứu public-role khỏi giả thuyết quyết định mô phỏng. | `human-track-ledger.jsonl` |
| 7. Report & audit | Render báo cáo chuyên nghiệp và validate trước khi bàn giao. | `validation-report.json`, final Markdown report |

## Năng lực cốt lõi

1. **Retrospective counterfactuals** — mô phỏng một thay đổi trong quá khứ có thể làm trạng thái lịch sử sau đó khác đi ra sao.
2. **Prospective interventions** — giữ baseline hiện tại cố định rồi dự phóng tương lai từ một can thiệp mới.
3. **Hybrid projections** — đưa một phân kỳ quá khứ tới hiện tại thay thế, rồi phân nhánh sang tương lai.
4. **Adaptive depth** — tăng độ sâu nghiên cứu theo độ phức tạp sự kiện, không dùng chế độ nhanh/chậm cố định.
5. **Mechanism-first causality** — loại bỏ cạnh nhân quả thiếu kênh truyền, lag, bối cảnh hoặc chứng cứ.
6. **Human-node discipline** — actor dossier dựa trên public-role; giả thuyết nhập vai luôn được gắn nhãn simulation.
7. **Future monitoring** — mỗi nhánh tương lai có leading indicators và điều kiện bác bỏ.
8. **Professional reporting** — báo cáo có executive summary, methodology, evidence quality, causal architecture, branch probabilities, sensitivity, limitations và audit appendix.
9. **Portable validation** — kiểm tra liên kết giữa evidence, node, edge, actor, branch, trace và report.

## Cấu trúc repo

```text
aleph-skill/
  SKILL.md
  AGENTS.md
  README.md
  README.vi.md
  LICENSE
  agents/openai.yaml
  adapters/
  examples/
  references/
  scripts/
  templates/
  package.json
  pyproject.toml
```

## Cài đặt

```powershell
git clone https://github.com/d-init-d/aleph-skill.git
cd aleph-skill
```

Dry-run cài adapter:

```powershell
python scripts\install_adapters.py --target codex --scope user --dry-run
python scripts\install_adapters.py --target claude-code --scope user --dry-run
python scripts\install_adapters.py --target opencode --scope user --dry-run
python scripts\install_adapters.py --target agents --scope user --dry-run
```

Đường dẫn skill phổ biến:

| Runtime | User / global path | Project path |
|---|---|---|
| Codex | `~/.codex/skills/aleph-skill` | tùy runtime |
| Claude Code | `~/.claude/skills/aleph-skill` | `.claude/skills/aleph-skill` |
| OpenCode | `~/.config/opencode/skills/aleph-skill` | `.opencode/skills/aleph-skill` |
| Generic Agent Skills | `~/.agents/skills/aleph-skill` | `.agents/skills/aleph-skill` |

## Kiểm tra

```powershell
python scripts\validate_skill_package.py .
python scripts\validate_simulation_artifacts.py --examples
python scripts\preflight.py --json
npm run self-test
```

Với một workspace mô phỏng đã hoàn tất:

```powershell
python scripts\validate_simulation_artifacts.py --workspace <run-dir> --mode draft --write-report
python scripts\render_simulation_report.py --workspace <run-dir>
python scripts\validate_simulation_artifacts.py --workspace <run-dir> --mode final --require-report --write-report
python scripts\evaluate_simulation_quality.py --workspace <run-dir> --threshold 90 --enforce
```

## Prompt mẫu

```text
Use $aleph-skill to simulate an oil price +40% shock starting June 2026.
Focus on inflation, central-bank reaction, growth, shipping, and emerging markets over 24 months.
Use D Research where available for the evidence layer, keep sourced actor dossiers separate from simulated decisions,
and produce at least three branches with probabilities, indicators, contradictions, and uncertainty warnings.
```

## Giới hạn an toàn

Aleph Skill dùng cho phân tích kịch bản hợp pháp và có chứng cứ. Skill từ chối hoặc thu hẹp các yêu cầu liên quan tới profiling người riêng tư, doxxing, stalking, trẻ vị thành niên, tài khoản riêng tư, vượt access control, captcha/paywall bypass hoặc suy đoán đặc điểm nhạy cảm không có chứng cứ.

Skill không tiên tri tương lai. Nó dựng mô phỏng minh bạch để người dùng kiểm tra giả định, cơ chế, bất định và phương án thay thế.

## License

Source-available cho mục đích phi thương mại theo [Creative Commons Attribution-NonCommercial 4.0 International License](https://creativecommons.org/licenses/by-nc/4.0/).

SPDX-License-Identifier: `CC-BY-NC-4.0`
