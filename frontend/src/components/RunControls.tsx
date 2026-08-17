import { Plus, Settings } from "lucide-react";

type Props = {
  isBusy: boolean;
  onCreate: () => void;
  onOpenSettings: () => void;
};

export function RunControls({ isBusy, onCreate, onOpenSettings }: Props) {
  return (
    <section className="panel sidebar-card">
      <h2>运行控制</h2>
      <button className="primary-button full-width" disabled={isBusy} onClick={onCreate}>
        <Plus size={15} />
        创建研究
      </button>
      <div className="project-info">
        <h3>项目信息</h3>
        <dl>
          <div><dt>项目 ID</dt><dd>PRJ-AISCI-DEMO</dd></div>
          <div><dt>负责人</dt><dd>研究员</dd></div>
          <div><dt>模式</dt><dd>证据约束</dd></div>
        </dl>
        <button className="secondary-button full-width" disabled={isBusy} onClick={onOpenSettings}><Settings size={14} /> 项目设置</button>
      </div>
    </section>
  );
}
