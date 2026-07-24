# Aleph Skill

**Bộ mô phỏng dòng thời gian dựa trên chứng cứ cho mọi AI CLI/IDE: từ một điểm thay đổi, dựng quá khứ phản thực, hiện tại thay thế và các tương lai có định lượng bất định.**

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Release](https://img.shields.io/github/v/release/d-init-d/aleph-skill?sort=semver)](https://github.com/d-init-d/aleph-skill/releases)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-portable-6f42c1)](https://agentskills.io/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Self-test](https://img.shields.io/badge/self--test-npm%20run%20self--test-brightgreen)](README.md#verification)

Aleph Skill biến câu hỏi “nếu như?” thành mô hình có thể kiểm toán: chứng cứ tạo thành cấu trúc nhân quả có kiểu, trace có thể replay và nhiều timeline với chế độ likelihood được khai báo rõ.

Aleph có một core dùng chung, trung lập với host. Codex, OpenCode, Claude Code, Agent Skills, Gemini/Copilot CLI, Cursor, VS Code, Windsurf, Cline, Roo Code và JetBrains có thể nạp thư mục skill native. Continue dùng project rule được sinh tự động. Grok Build, Aider và CLI tùy biến dùng profile adapter dạng hợp đồng để host hoặc wrapper triển khai.

## Tổng quan

| Hạng mục | Aleph Skill cung cấp |
|---|---|
| Mục đích chính | Lịch sử phản thực, can thiệp ở hiện tại, timeline lai quá khứ–hiện tại–tương lai, và mô phỏng hiệu ứng cánh bướm. |
| Mô hình mô phỏng | Node có chứng cứ, cạnh có cơ chế, trace replay được, relative weight, calibrated probability và unresolved mass. |
| Yếu tố con người | Hồ sơ evidence, mixed hoặc assumption-only cấp dữ liệu cho roleplay đã niêm phong; roleplay không bao giờ được xem là chứng cứ. |
| Độ sâu nghiên cứu | Tự mở rộng theo quy mô sự kiện, địa lý, số tác nhân, độ sâu nhân quả, mức bất định và hệ trọng. |
| Đầu ra | Báo cáo chuyên nghiệp, evidence map, causal graph, branch ledger, propagation trace, validation report và audit metadata. |
| Tính portable | Skill markdown gọn, script ưu tiên Python stdlib, adapter cho nhiều môi trường agent. |
| Phạm vi sáng tạo | Aleph không có danh sách chủ đề bị từ chối; chi tiết chưa có chứng cứ được gắn nhãn assumption hoặc simulation. |

## Khi nào nên dùng

Dùng Aleph Skill khi agent cần:

- dựng lại baseline trước một điểm thay đổi;
- mô phỏng một thay đổi trong quá khứ sẽ làm hiện tại khác đi như thế nào;
- giả định một can thiệp ở hiện tại rồi dự phóng nhiều nhánh tương lai;
- phân tích kịch bản chính sách, thị trường, địa chính trị, xã hội, khí hậu, công nghệ hoặc tổ chức;
- lần theo hiệu ứng cánh bướm qua chuỗi nhân quả, ngưỡng kích hoạt, feedback loop và độ trễ;
- mô hình hóa sáng tạo mọi quyết định con người nhưng không biến giả định mô phỏng thành sự thật quan sát;
- tạo báo cáo tách rõ `fact`, `inference`, `simulation` và `counterfactual`.

Mọi kịch bản sáng tạo đều hợp lệ trong Aleph. Chính sách bắt buộc về công cụ hoặc truy cập của host là lớp bên ngoài, không phải giới hạn nội dung của Aleph.

## Phạm vi sản phẩm

Đây là một Agent Skill portable, không phải dịch vụ dự báo, public Python API, API server, crawler hay bảng xếp hạng benchmark. Module Python cài kèm là helper thực thi nội bộ có version, không phải bề mặt thư viện ổn định cho phần mềm bên thứ ba.

Agent đọc `SKILL.md` làm điểm vào, sau đó chỉ tải các reference và template cần cho kịch bản. Helper cục bộ đảm nhiệm khởi tạo/migrate workspace, import research đã ký, compile/run/replay mô hình, sensitivity, hindcast, validate domain pack/artifact, render báo cáo, finalize receipt và kiểm tra package phân phối.

Từ **2.1.0**, [D Research](https://github.com/d-init-d/d-research-skill) được nhúng thành component nội bộ có khóa digest tại `aleph-component://d-research`. Host chỉ cần cài và nạp `aleph-skill`; mọi lời gọi nghiên cứu đi qua `scripts/research_gateway.py`, không cần cài skill D Research thứ hai. Aleph không đóng gói `node_modules`, Chromium hoặc thông tin xác thực và không tự cài chúng. Khi thiếu capability, gateway hạ cấp theo chuỗi browser → host browser → fetch → search → structured blocker; fallback dùng công cụ hợp pháp của host bị giới hạn ở assurance `limited` và không được giả lập ledger có chữ ký hoặc import receipt.

Profile cho CLI ngoài chỉ mô tả probe phiên bản, bootstrap, ranh giới capability, yêu cầu cô lập và receipt. Việc cài profile không tự tạo subagent, tool isolation hay orchestration; host hoặc wrapper phải triển khai và xác nhận các kiểm soát đó.

## Vòng đời workflow

| Phase | Việc diễn ra | Artifact chính |
|---|---|---|
| 0. Frame | Xác định điểm thay đổi, observation cutoff, horizon, địa lý, domain và temporal mode. | `simulation-manifest.json` |
| 1. Research | Dựng baseline, source map, evidence map, contradiction notes và uncertainty register. | `evidence-map.csv` |
| 2. Construct | Tạo entity, event, factor, context, indicator, claim, source và actor nodes. | `timeline-node.json`, `actor-dossier.json` |
| 3. Link | Chỉ nhận cạnh nhân quả có cơ chế, độ trễ, bối cảnh, chứng cứ, strength và confidence. | `causal-edge.json` |
| 4. Propagate | Lan truyền tác động qua transform phi tuyến, stock decay bất biến theo timestep và tích phân flow dạng rate/impulse theo formula 2.1. | `propagation-trace.jsonl` |
| 5. Branch | Tạo timeline khác biệt bằng relative weight; chỉ dùng xác suất khi calibration gate cho phép. | `branch-ledger.json` |
| 6. Human decisions | Tách research của actor evidence/mixed khỏi roleplay; actor assumption-only bỏ qua research. | `human-track-ledger.jsonl` |
| 7. Report & audit | Render báo cáo chuyên nghiệp và validate trước khi bàn giao. | `validation-report.json`, final Markdown report |

## Năng lực cốt lõi

1. **Retrospective counterfactuals** — mô phỏng một thay đổi trong quá khứ có thể làm trạng thái lịch sử sau đó khác đi ra sao.
2. **Prospective interventions** — giữ baseline hiện tại cố định rồi dự phóng tương lai từ một can thiệp mới.
3. **Hybrid projections** — đưa một phân kỳ quá khứ tới hiện tại thay thế, rồi phân nhánh sang tương lai.
4. **Adaptive depth** — tăng độ sâu nghiên cứu theo độ phức tạp sự kiện, không dùng chế độ nhanh/chậm cố định.
5. **Mechanism-first causality** — loại bỏ cạnh nhân quả thiếu kênh truyền, lag, bối cảnh hoặc chứng cứ.
6. **Human-node discipline** — khai báo `actor_basis`, tách chứng cứ khỏi sáng tạo và luôn gắn nhãn roleplay là simulation.
7. **Future monitoring** — mỗi nhánh tương lai có leading indicators và điều kiện bác bỏ.
8. **Professional reporting** — báo cáo likelihood mode, evidence quality, causal architecture, sensitivity, unresolved mass, giới hạn và receipt kiểm toán.
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
  components/d-research/
  component-lock.json
  examples/
  references/
  scripts/
  templates/
  THIRD_PARTY_NOTICES.md
  package.json
  pyproject.toml
```

## Cài đặt

```powershell
git clone https://github.com/d-init-d/aleph-skill.git
$env:ALEPH_SKILL_ROOT = (Resolve-Path ".\aleph-skill").Path
```

Với POSIX shell, đặt cùng quy ước đường dẫn tuyệt đối bằng `export ALEPH_SKILL_ROOT="$(cd aleph-skill && pwd)"`. Host native lấy thư mục chứa `SKILL.md`; adapter project thường lấy `<project>/.aleph/core/aleph-skill`. Helper của Aleph không phụ thuộc current working directory.

Dry-run cài adapter:

```powershell
python "$env:ALEPH_SKILL_ROOT\scripts\install_adapters.py" --target codex --scope user --dry-run
python "$env:ALEPH_SKILL_ROOT\scripts\install_adapters.py" --target claude-code --scope user --dry-run
python "$env:ALEPH_SKILL_ROOT\scripts\install_adapters.py" --target opencode --scope user --dry-run
python "$env:ALEPH_SKILL_ROOT\scripts\install_adapters.py" --target agents --scope user --dry-run
```

Gemini CLI dùng trực tiếp thư mục Agent Skills chuẩn:

```powershell
python "$env:ALEPH_SKILL_ROOT\scripts\install_adapters.py" --target gemini-cli --scope user --copy
```

Target skill native cài package đã xác minh vào thư mục skill đã khai báo. Continue và adapter CLI ngoài chỉ cài ở scope project: installer chép rule/profile cùng core đã xác minh vào `.aleph/core/aleph-skill`, sau đó ghi một receipt chung. Profile ngoài vẫn là hợp đồng adapter, không phải orchestration turnkey.

```powershell
python "$env:ALEPH_SKILL_ROOT\scripts\install_adapters.py" --target cursor --scope project --project-dir <project> --copy --receipt <project>\.aleph\install-receipt.json
python "$env:ALEPH_SKILL_ROOT\scripts\install_adapters.py" --target grok-build --scope project --project-dir <project> --copy --receipt <project>\.aleph\install-receipt.json
```

Các vị trí cài đặt được hỗ trợ:

| Runtime | User / global path | Project path |
|---|---|---|
| Codex | `~/.agents/skills/aleph-skill` | `.agents/skills/aleph-skill` |
| Claude Code | `~/.claude/skills/aleph-skill` | `.claude/skills/aleph-skill` |
| OpenCode | `~/.config/opencode/skills/aleph-skill` | `.opencode/skills/aleph-skill` |
| Gemini CLI | `~/.gemini/skills/aleph-skill` | `.gemini/skills/aleph-skill` |
| GitHub Copilot CLI / VS Code | `~/.copilot/skills/aleph-skill` | `.github/skills/aleph-skill` |
| Cline | `~/.cline/skills/aleph-skill` | `.cline/skills/aleph-skill` |
| Roo Code | `~/.roo/skills/aleph-skill` | `.roo/skills/aleph-skill` |
| Cursor | `~/.cursor/skills/aleph-skill` | `.cursor/skills/aleph-skill` |
| Windsurf | `~/.codeium/windsurf/skills/aleph-skill` | `.windsurf/skills/aleph-skill` |
| JetBrains AI Assistant | IDE quản lý theo sản phẩm/OS | `.agents/skills/aleph-skill` |
| Continue | không có | `.continue/rules/aleph.md` và `.aleph/core/aleph-skill` |
| Generic Agent Skills | `~/.agents/skills/aleph-skill` | `.agents/skills/aleph-skill` |
| Grok Build / Aider / generic CLI | không có | `.aleph/profiles/<target>.json` và `.aleph/core/aleph-skill` |

## Kiểm tra

Khi nâng một workspace 2.0.0 hiện có, hãy giữ nguyên một bản sao lưu và chạy validation ở chế độ draft trước. Aleph 2.2.x vẫn dùng `schema_version: 2.0.0`, ghi `formula_version: 2.1.0` cho workspace mới và vẫn replay được artifact công thức 2.0.0. Các contract số học, binding component, likelihood, provenance và sealed roleplay chặt hơn có thể yêu cầu tạo lại report, packet/receipt và numerical artifact trước khi final validation đạt. Với workspace còn lưu đường dẫn D Research tuyệt đối, chạy `python "<ALEPH_SKILL_ROOT>/scripts/migrate_workspace.py" --source <workspace> --bind-bundled-d-research --check`, xem báo cáo tương thích byte, rồi chạy lại không có `--check`. Không dùng migrator 1.x hoặc sửa hash hay formula identifier bằng tay.

```powershell
python "$env:ALEPH_SKILL_ROOT\scripts\validate_skill_package.py" "$env:ALEPH_SKILL_ROOT"
python "$env:ALEPH_SKILL_ROOT\scripts\validate_simulation_artifacts.py" --examples
python "$env:ALEPH_SKILL_ROOT\scripts\preflight.py" --json
npm --prefix "$env:ALEPH_SKILL_ROOT" run self-test
```

Maintainer có thể tiếp tục tạo release asset deterministic, khớp chính xác distribution manifest:

```powershell
npm --prefix "$env:ALEPH_SKILL_ROOT" run release:build
```

Với một workspace mô phỏng đã hoàn tất:

```powershell
python "$env:ALEPH_SKILL_ROOT\scripts\validate_simulation_artifacts.py" --workspace <run-dir> --mode draft --write-report
python "$env:ALEPH_SKILL_ROOT\scripts\render_simulation_report.py" --workspace <run-dir>
python "$env:ALEPH_SKILL_ROOT\scripts\validate_simulation_artifacts.py" --workspace <run-dir> --mode final --require-report --write-report
python "$env:ALEPH_SKILL_ROOT\scripts\evaluate_simulation_quality.py" --workspace <run-dir> --threshold 90 --enforce
```

## Prompt mẫu

```text
Use $aleph-skill to simulate an oil price +40% shock, with both the observation cutoff and shock start set to 2026-06-01.
Focus on inflation, central-bank reaction, growth, shipping, and emerging markets over 24 months.
Use the bundled D Research component through Aleph's gateway, falling back to limited host-native evidence only when the gateway reports a missing capability; keep sourced actor dossiers separate from simulated decisions,
and produce at least three branches with relative weights, indicators, contradictions, and uncertainty warnings.
```

## Tự do mô phỏng

Aleph luôn chạy kịch bản được yêu cầu và không tự từ chối, thu hẹp hay thay thế kịch bản vì chủ đề, nhân vật, động cơ, xung đột, bạo lực, tôn giáo, chính trị hoặc tính giả định. Nội dung chưa được chứng minh được ghi là `assumption`, `simulation` hoặc `counterfactual`, không bị dùng làm lý do dừng mô phỏng.

Chính sách bắt buộc của host và giới hạn truy cập của công cụ nằm ngoài Aleph. Chúng không được mô tả thành giới hạn của skill; khi thiếu nguồn hoặc công cụ, Aleph tiếp tục theo tuyến assumption-first và hạ assurance tương ứng.

Skill không tiên tri tương lai. Nó dựng mô phỏng minh bạch để người dùng kiểm tra giả định, cơ chế, bất định và phương án thay thế.

## License

Source-available cho mục đích phi thương mại theo [Creative Commons Attribution-NonCommercial 4.0 International License](https://creativecommons.org/licenses/by-nc/4.0/).

SPDX-License-Identifier: `CC-BY-NC-4.0`
