import type { EventRecord } from "../api/types";

type SkillInvocation = {
  eventId: string;
  stepId: string;
  agentId: string;
  skillId: string;
  description: string;
  trigger: string;
  loadMode: string;
  authorizedTools: string[];
};

function eventState(event: EventRecord) {
  const status = String(event.output_summary?.status ?? event.output_summary?.verdict ?? "");
  const accepted = event.output_summary?.accepted;
  const message = event.message.toLowerCase();
  if (
    event.level === "error"
    || status === "failed"
    || accepted === false
    || message.includes("failed")
    || message.includes("rejected")
  ) {
    return { label: "执行失败", className: "mini-tag error" };
  }
  if (event.fallback_used) return { label: "已降级", className: "mini-tag warn" };
  return { label: "已完成", className: "mini-tag" };
}

function skillInvocations(events: EventRecord[]): SkillInvocation[] {
  const rows: SkillInvocation[] = [];
  for (const event of events) {
    for (const call of event.tool_calls ?? []) {
      if (call.provider !== "skill_runtime") continue;
      const agentId = String(call.agent_id ?? event.actor);
      const invocations = Array.isArray(call.skill_invocations)
        ? call.skill_invocations
        : (Array.isArray(call.skills)
          ? call.skills.map((skillId) => ({
              skill_id: skillId,
              trigger: "legacy",
              load_mode: "historical",
              authorized_tools: call.authorized_tools,
            }))
          : []);
      for (const value of invocations) {
        if (!value || typeof value !== "object") continue;
        const item = value as Record<string, unknown>;
        rows.push({
          eventId: event.id,
          stepId: String(call.step_id ?? event.step_id),
          agentId,
          skillId: String(item.skill_id ?? item.name ?? ""),
          description: String(item.description ?? ""),
          trigger: String(item.trigger ?? "required"),
          loadMode: String(item.load_mode ?? "complete"),
          authorizedTools: Array.isArray(item.authorized_tools)
            ? item.authorized_tools.map(String)
            : [],
        });
      }
    }
  }
  return rows;
}

export function AgentTrace({ events }: { events: EventRecord[] }) {
  const skills = skillInvocations(events).slice(-12);
  return (
    <section className="panel section-card trace-panel">
      <div className="section-title"><span>F</span><h2>智能体轨迹</h2></div>
      <div className="trace-table-scroll">
        <table className="trace-table">
          <thead><tr><th>步骤</th><th>智能体</th><th>内容</th><th>状态</th></tr></thead>
          <tbody>
            {events.length === 0 ? (
              <tr>
                <td>0</td>
                <td>等待</td>
                <td>等待真实执行记录</td>
                <td><span className="mini-tag muted">等待中</span></td>
              </tr>
            ) : events.slice(-7).map((event, index) => {
              const status = eventState(event);
              return (
                <tr key={event.id}>
                  <td>{index + 1}</td>
                  <td>{event.actor}</td>
                  <td>{event.message}</td>
                  <td><span className={status.className}>{status.label}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="skill-trace-heading">
        <h3>本次实际调用的 Skills</h3>
        <span>{skills.length} 条</span>
      </div>
      <div className="trace-table-scroll">
        <table className="trace-table skill-trace-table">
          <thead>
            <tr><th>阶段</th><th>Skill</th><th>触发</th><th>加载</th><th>授权工具</th></tr>
          </thead>
          <tbody>
            {skills.length === 0 ? (
              <tr><td colSpan={5} className="trace-empty">尚无 Skill 调用记录</td></tr>
            ) : skills.map((skill, index) => (
              <tr key={`${skill.eventId}-${skill.skillId}-${index}`} title={skill.description}>
                <td>{skill.stepId}</td>
                <td><strong>{skill.skillId}</strong><small>{skill.agentId}</small></td>
                <td>{skill.trigger === "conditional" ? "条件触发" : skill.trigger === "legacy" ? "历史记录" : "必需"}</td>
                <td>{skill.loadMode === "complete" ? "完整" : skill.loadMode}</td>
                <td>{skill.authorizedTools.join("、") || "只读指令"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {events.length ? (
        <details className="trace-details">
          <summary>查看最近一次完整工具调用</summary>
          <pre className="compact-json">{JSON.stringify(events[events.length - 1].tool_calls, null, 2)}</pre>
        </details>
      ) : null}
    </section>
  );
}
