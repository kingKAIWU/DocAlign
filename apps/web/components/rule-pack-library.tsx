"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError, apiUrl } from "@/lib/api";
import type {
  FormattingSpec,
  RulePackApprovalStatus,
  RulePackArtifact,
  RulePackCatalogItem,
  RulePackDetail,
} from "@/lib/types";

type RulePackLibraryProps = {
  specText: string;
  disabled: boolean;
  onApply: (artifact: RulePackArtifact) => void;
};

type PendingWrite = { key: string; requestId: string };

const rulePackErrors: Record<string, string> = {
  RULE_PACK_NAME_CONFLICT: "已有同名规则包，请选择该规则包保存新修订，或更换名称。",
  RULE_PACK_NOT_FOUND: "规则包已不存在，请重新加载列表。",
  RULE_PACK_VERSION_NOT_FOUND: "所选修订已不存在，请重新加载版本历史。",
  RULE_PACK_INTEGRITY_FAILED: "规则包完整性校验失败，已阻止载入；请保留数据并导出诊断信息。",
  RULE_PACK_VERSION_CONFLICT: "规则包刚刚发生变化，请重新加载后再保存。",
  IDEMPOTENCY_KEY_REUSED: "同一保存请求对应了不同内容，已阻止写入；请重新操作。",
};

function readableRulePackError(caught: unknown): string {
  if (caught instanceof SyntaxError) return "当前高级规则 JSON 无法解析，暂不能保存。";
  if (caught instanceof ApiError) return rulePackErrors[caught.code] ?? caught.message;
  return caught instanceof Error ? caught.message : "规则包操作失败。";
}

function newRequestId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `rule_${Date.now()}_${Math.random().toString(36).slice(2, 14)}`;
}

export function RulePackLibrary({ specText, disabled, onApply }: RulePackLibraryProps) {
  const [catalog, setCatalog] = useState<RulePackCatalogItem[]>([]);
  const [selectedPackId, setSelectedPackId] = useState("");
  const [detail, setDetail] = useState<RulePackDetail | null>(null);
  const [selectedRevision, setSelectedRevision] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [scopeLabel, setScopeLabel] = useState("");
  const [changeNote, setChangeNote] = useState("创建初始修订");
  const [locallyApproved, setLocallyApproved] = useState(false);
  const [approvalNote, setApprovalNote] = useState("");
  const [busy, setBusy] = useState<string | null>("catalog");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [reloadCount, setReloadCount] = useState(0);
  const pendingWriteRef = useRef<PendingWrite | null>(null);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    api.rulePacks(controller.signal)
      .then((result) => {
        if (!active) return;
        setCatalog(result.rule_packs);
        if (result.rule_packs.length === 0) {
          setDetail(null);
          setSelectedRevision(null);
        }
        setSelectedPackId((current) =>
          current && result.rule_packs.some((item) => item.pack_id === current)
            ? current
            : result.rule_packs[0]?.pack_id ?? "",
        );
        setError("");
      })
      .catch((caught) => {
        if (!controller.signal.aborted) {
          setError(`规则包列表暂时无法连接；当前规则不受影响。${readableRulePackError(caught)}`);
        }
      })
      .finally(() => {
        if (active) setBusy(null);
      });
    return () => {
      active = false;
      controller.abort();
    };
  }, [reloadCount]);

  useEffect(() => {
    if (!selectedPackId) {
      return;
    }
    let active = true;
    const controller = new AbortController();
    api.rulePack(selectedPackId, controller.signal)
      .then((result) => {
        if (!active) return;
        setDetail(result);
        setSelectedRevision((current) =>
          current && result.versions.some((item) => item.revision === current)
            ? current
            : result.current_revision,
        );
        setError("");
      })
      .catch((caught) => {
        if (!controller.signal.aborted) setError(readableRulePackError(caught));
      })
    return () => {
      active = false;
      controller.abort();
    };
  }, [selectedPackId, reloadCount]);

  const selectedVersion = useMemo(
    () => detail?.versions.find((item) => item.revision === selectedRevision) ?? null,
    [detail, selectedRevision],
  );
  const approvalStatus: RulePackApprovalStatus = locallyApproved
    ? "locally_approved"
    : "draft";
  const controlsDisabled = disabled || Boolean(busy);

  function currentSpec(): FormattingSpec {
    return JSON.parse(specText) as FormattingSpec;
  }

  function requestIdFor(key: string): string {
    if (pendingWriteRef.current?.key === key) return pendingWriteRef.current.requestId;
    const requestId = newRequestId();
    pendingWriteRef.current = { key, requestId };
    return requestId;
  }

  function handleWriteError(caught: unknown) {
    if (caught instanceof ApiError) pendingWriteRef.current = null;
    setError(
      caught instanceof ApiError
        ? readableRulePackError(caught)
        : `连接中断，可直接重试；系统会复用本次请求标识，避免重复修订。${readableRulePackError(caught)}`,
    );
  }

  function finishWrite(artifact: RulePackArtifact, message: string) {
    pendingWriteRef.current = null;
    setNotice(message);
    setError("");
    setSelectedPackId(artifact.pack_id);
    setSelectedRevision(artifact.revision);
    setReloadCount((value) => value + 1);
  }

  async function createPack() {
    if (!name.trim() || !scopeLabel.trim()) {
      setError("请填写规则包名称和具体适用范围。 ");
      return;
    }
    if (locallyApproved && !approvalNote.trim()) {
      setError("标记为本地已确认时，请填写核对人、依据或核对说明。 ");
      return;
    }
    setBusy("create");
    setNotice("");
    setError("");
    const key = [
      "create",
      name,
      description,
      scopeLabel,
      changeNote,
      approvalStatus,
      approvalNote,
      specText,
    ].join("\u0000");
    try {
      const artifact = await api.createRulePack({
        request_id: requestIdFor(key),
        name,
        description,
        scope_label: scopeLabel,
        spec: currentSpec(),
        change_note: changeNote || "创建初始修订",
        approval_status: approvalStatus,
        approval_note: locallyApproved ? approvalNote : null,
      });
      finishWrite(artifact, `已保存“${artifact.name}”修订 ${artifact.revision}。`);
      setChangeNote("记录本次规则调整");
    } catch (caught) {
      handleWriteError(caught);
    } finally {
      setBusy(null);
    }
  }

  async function createVersion() {
    if (!detail) return;
    if (!changeNote.trim()) {
      setError("保存新修订前，请填写本次变更说明。 ");
      return;
    }
    if (locallyApproved && !approvalNote.trim()) {
      setError("标记为本地已确认时，请填写核对人、依据或核对说明。 ");
      return;
    }
    setBusy("version");
    setNotice("");
    setError("");
    const key = [
      "version",
      detail.pack_id,
      changeNote,
      approvalStatus,
      approvalNote,
      specText,
    ].join("\u0000");
    try {
      const artifact = await api.createRulePackVersion(detail.pack_id, {
        request_id: requestIdFor(key),
        spec: currentSpec(),
        change_note: changeNote,
        approval_status: approvalStatus,
        approval_note: locallyApproved ? approvalNote : null,
      });
      finishWrite(artifact, `已创建“${artifact.name}”修订 ${artifact.revision}。`);
    } catch (caught) {
      handleWriteError(caught);
    } finally {
      setBusy(null);
    }
  }

  async function loadVersion() {
    if (!detail || selectedRevision === null) return;
    setBusy("load");
    setNotice("");
    setError("");
    try {
      const artifact = await api.rulePackVersion(detail.pack_id, selectedRevision);
      onApply(artifact);
      setNotice(`已载入“${artifact.name}”修订 ${artifact.revision}；尚未修改源文档。`);
    } catch (caught) {
      setError(readableRulePackError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function restoreVersion() {
    if (!detail || selectedRevision === null || selectedRevision === detail.current_revision) return;
    if (!window.confirm(
      `将修订 ${selectedRevision} 恢复为一个新的草稿修订；现有历史不会被覆盖。是否继续？`,
    )) return;
    const restoreNote = `从修订 ${selectedRevision} 恢复；需要重新确认适用范围`;
    const key = ["restore", detail.pack_id, selectedRevision, restoreNote].join("\u0000");
    setBusy("restore");
    setNotice("");
    setError("");
    try {
      const artifact = await api.restoreRulePackVersion(
        detail.pack_id,
        selectedRevision,
        restoreNote,
        requestIdFor(key),
      );
      onApply(artifact);
      finishWrite(
        artifact,
        `已从修订 ${selectedRevision} 恢复为修订 ${artifact.revision}；状态为草稿，需重新核对。`,
      );
    } catch (caught) {
      handleWriteError(caught);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rule-pack-library" role="tabpanel">
      <div className="rule-pack-intro">
        <strong>可复用的本地规则包</strong>
        <p>保存当前结构化规则，跨文档复用并保留不可变修订历史。规则包只保存在本机。</p>
        <small>“本地已确认”仅表示你记录了人工核对，不代表机构认证或法规合规。</small>
      </div>

      <label htmlFor="saved-rule-pack">已保存规则包</label>
      <div className="rule-pack-select-row">
        <select
          id="saved-rule-pack"
          value={selectedPackId}
          disabled={controlsDisabled || catalog.length === 0}
          onChange={(event) => {
            setSelectedPackId(event.target.value);
            setSelectedRevision(null);
            setNotice("");
          }}
        >
          {catalog.length === 0 ? (
            <option value="">尚未保存规则包</option>
          ) : catalog.map((item) => (
            <option value={item.pack_id} key={item.pack_id}>
              {item.name} · 修订 {item.current_revision}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="button secondary compact"
          disabled={controlsDisabled}
          onClick={() => {
            setBusy("catalog");
            setReloadCount((value) => value + 1);
          }}
        >
          重新加载
        </button>
      </div>

      {detail && (
        <div className="rule-pack-history" aria-label="规则包版本历史">
          <div>
            <strong>{detail.name}</strong>
            <span>{detail.scope_label}</span>
            {detail.description && <small>{detail.description}</small>}
          </div>
          <label htmlFor="saved-rule-revision">修订版本</label>
          <select
            id="saved-rule-revision"
            value={selectedRevision ?? ""}
            disabled={controlsDisabled}
            onChange={(event) => setSelectedRevision(Number(event.target.value))}
          >
            {detail.versions.map((version) => (
              <option value={version.revision} key={version.revision}>
                修订 {version.revision}{version.revision === detail.current_revision ? " · 当前" : ""}
                {version.approval_status === "locally_approved" ? " · 本地已确认" : " · 草稿"}
              </option>
            ))}
          </select>
          {selectedVersion && (
            <div className={`rule-pack-version-note ${selectedVersion.approval_status}`}>
              <strong>
                {selectedVersion.approval_status === "locally_approved" ? "本地已确认" : "草稿"}
              </strong>
              <span>{selectedVersion.change_note}</span>
              <small>
                SHA-256 {selectedVersion.spec_sha256.slice(0, 12)}… · {new Date(selectedVersion.created_at).toLocaleString("zh-CN")}
              </small>
              {selectedVersion.restored_from_revision && (
                <small>由修订 {selectedVersion.restored_from_revision} 安全恢复</small>
              )}
              {selectedVersion.approval_note && <small>核对记录：{selectedVersion.approval_note}</small>}
            </div>
          )}
          <div className="rule-pack-actions">
            <button type="button" className="button primary" disabled={controlsDisabled} onClick={loadVersion}>
              {busy === "load" ? "载入中…" : "载入所选修订"}
            </button>
            <a
              className={`button secondary ${controlsDisabled || selectedRevision === null ? "disabled" : ""}`}
              href={
                !controlsDisabled && selectedRevision !== null
                  ? apiUrl(`/api/v1/rule-packs/${detail.pack_id}/versions/${selectedRevision}/export`)
                  : undefined
              }
              aria-disabled={controlsDisabled || selectedRevision === null}
              download
            >
              导出 JSON
            </a>
            <button
              type="button"
              className="button secondary"
              disabled={controlsDisabled || selectedRevision === detail.current_revision}
              onClick={restoreVersion}
            >
              {busy === "restore" ? "恢复中…" : "恢复为新修订"}
            </button>
          </div>
        </div>
      )}

      <details className="rule-pack-save" open={catalog.length === 0}>
        <summary>{detail ? "保存当前规则" : "创建第一个规则包"}</summary>
        <div className="rule-pack-fields">
          <label htmlFor="rule-pack-name">规则包名称</label>
          <input
            id="rule-pack-name"
            value={name}
            maxLength={120}
            placeholder="例如：综合办公室月报格式"
            onChange={(event) => setName(event.target.value)}
          />
          <label htmlFor="rule-pack-scope">具体适用范围</label>
          <input
            id="rule-pack-scope"
            value={scopeLabel}
            maxLength={240}
            placeholder="例如：2026 年综合办公室内部月报"
            onChange={(event) => setScopeLabel(event.target.value)}
          />
          <label htmlFor="rule-pack-description">说明（可选）</label>
          <textarea
            id="rule-pack-description"
            value={description}
            maxLength={2000}
            placeholder="记录模板来源、未覆盖内容或使用限制"
            onChange={(event) => setDescription(event.target.value)}
          />
          <label htmlFor="rule-pack-change-note">本次变更说明</label>
          <input
            id="rule-pack-change-note"
            value={changeNote}
            maxLength={1000}
            onChange={(event) => setChangeNote(event.target.value)}
          />
          <label className="rule-pack-approval">
            <input
              type="checkbox"
              checked={locallyApproved}
              onChange={(event) => setLocallyApproved(event.target.checked)}
            />
            <span>
              <strong>标记为本地已确认</strong>
              <small>我已人工核对规则来源、适用范围和当前高级规则</small>
            </span>
          </label>
          {locallyApproved && (
            <input
              aria-label="本地核对记录"
              value={approvalNote}
              maxLength={1000}
              placeholder="核对人、日期和依据"
              onChange={(event) => setApprovalNote(event.target.value)}
            />
          )}
          <div className="rule-pack-save-actions">
            <button type="button" className="button primary" disabled={controlsDisabled} onClick={createPack}>
              {busy === "create" ? "保存中…" : "另存为新规则包"}
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={controlsDisabled || !detail}
              onClick={createVersion}
            >
              {busy === "version" ? "保存中…" : "保存到所选包的新修订"}
            </button>
          </div>
        </div>
      </details>

      {(notice || error) && (
        <p className={`rule-pack-message ${error ? "error" : "success"}`} role="status">
          {error || notice}
        </p>
      )}
    </div>
  );
}
