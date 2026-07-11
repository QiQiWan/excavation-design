import { useEffect, useMemo, useState } from 'react';

function num(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function zoneFill(zone: Record<string, any>): string {
  if (zone.status === 'fail') return '#fee2e2';
  if (zone.status === 'warning' || zone.status === 'manual_review') return '#fef3c7';
  if (zone.zoneType === 'support_node_zone') return '#dbeafe';
  if (zone.zoneType === 'excavation_transition_zone') return '#e0e7ff';
  if (zone.zoneType === 'toe_zone') return '#dcfce7';
  return '#f8fafc';
}

export function WallZoneElevationPreview({ zones }: { zones: Record<string, any>[] }) {
  const hosts = useMemo(() => Array.from(new Set(zones.map((item) => String(item.hostCode)))), [zones]);
  const [host, setHost] = useState(hosts[0] ?? '');
  useEffect(() => { if (!hosts.includes(host)) setHost(hosts[0] ?? ''); }, [hosts, host]);
  const rows = useMemo(() => zones.filter((item) => String(item.hostCode) === host).sort((a, b) => num(b.topElevation) - num(a.topElevation)), [zones, host]);
  const top = Math.max(...rows.map((item) => num(item.topElevation)), 0);
  const bottom = Math.min(...rows.map((item) => num(item.bottomElevation)), -10);
  const span = Math.max(top - bottom, 1);
  const y = (elevation: number) => 35 + ((top - elevation) / span) * 340;
  return (
    <div className="rebarDrawingPreview">
      <div className="previewHeader"><strong>墙体分区配筋立面预览</strong><label>墙段 <select value={host} onChange={(event) => setHost(event.target.value)}>{hosts.map((item) => <option key={item}>{item}</option>)}</select></label></div>
      {rows.length ? <svg viewBox="0 0 760 420" role="img" aria-label="墙体分区配筋立面预览">
        <rect x="74" y="35" width="190" height="340" fill="#f1f5f9" stroke="#334155" strokeWidth="2" />
        {rows.map((zone) => {
          const yTop = y(num(zone.topElevation)); const yBottom = y(num(zone.bottomElevation));
          const faces = (zone.faces ?? []) as Record<string, any>[];
          const inner = faces.find((item) => item.face === 'inner'); const outer = faces.find((item) => item.face === 'outer');
          return <g key={String(zone.zoneId)}>
            <rect x="75" y={yTop} width="188" height={Math.max(yBottom - yTop, 2)} fill={zoneFill(zone)} stroke="#94a3b8" />
            <text x="82" y={(yTop + yBottom) / 2 - 6} fontSize="10" fill="#0f172a">{String(zone.zoneId)}</text>
            <text x="82" y={(yTop + yBottom) / 2 + 8} fontSize="9" fill="#334155">IN {String(inner?.token ?? '-')}</text>
            <text x="82" y={(yTop + yBottom) / 2 + 20} fontSize="9" fill="#334155">OUT {String(outer?.token ?? '-')}</text>
            <line x1="264" y1={(yTop + yBottom) / 2} x2="292" y2={(yTop + yBottom) / 2} stroke="#64748b" />
            <text x="300" y={(yTop + yBottom) / 2 - 5} fontSize="10" fill="#0f172a">{String(zone.zoneType)}</text>
            <text x="300" y={(yTop + yBottom) / 2 + 8} fontSize="9" fill="#475569">EL {num(zone.topElevation).toFixed(2)} ~ {num(zone.bottomElevation).toFixed(2)} m</text>
            <text x="520" y={(yTop + yBottom) / 2 + 2} fontSize="9" fill={zone.status === 'fail' ? '#b91c1c' : '#475569'}>{String(zone.status)} · {(zone.drawingRefs ?? []).join('/')}</text>
          </g>;
        })}
        <text x="20" y="38" fontSize="10" fill="#475569">{top.toFixed(2)} m</text>
        <text x="20" y="378" fontSize="10" fill="#475569">{bottom.toFixed(2)} m</text>
        <text x="74" y="402" fontSize="10" fill="#64748b">色块表示配筋区；红色为截面或配筋体系升级项。正式施工图以 R-02 单墙图和配筋表为准。</text>
      </svg> : <span className="small">当前没有可预览的墙体分区。</span>}
    </div>
  );
}

export function SupportRebarPreview({ rows }: { rows: Record<string, any>[] }) {
  const codes = useMemo(() => rows.filter((item) => item.section).map((item) => String(item.hostCode)), [rows]);
  const [code, setCode] = useState(codes[0] ?? '');
  useEffect(() => { if (!codes.includes(code)) setCode(codes[0] ?? ''); }, [codes, code]);
  const row = rows.find((item) => String(item.hostCode) === code && item.section);
  if (!row) return <div className="rebarDrawingPreview"><strong>支撑配筋示意</strong><p className="small">当前项目没有钢筋混凝土支撑配筋数据。</p></div>;
  const span = Math.max(num(row.spanM, 10), 1);
  const endLength = Math.min(num(row.endZones?.lengthM, 1.5), span / 2);
  const endRatio = Math.max(0.08, Math.min(0.35, endLength / span));
  const leftEnd = 70 + 610 * endRatio;
  const rightStart = 680 - 610 * endRatio;
  return <div className="rebarDrawingPreview">
    <div className="previewHeader"><strong>支撑端部—跨中分区配筋示意</strong><label>支撑 <select value={code} onChange={(event) => setCode(event.target.value)}>{codes.slice(0, 120).map((item) => <option key={item}>{item}</option>)}</select></label></div>
    <svg viewBox="0 0 760 250" role="img" aria-label="支撑分区配筋示意">
      <rect x="70" y="70" width="610" height="88" rx="4" fill="#f8fafc" stroke="#334155" strokeWidth="2" />
      <rect x="70" y="70" width={leftEnd - 70} height="88" fill="#dbeafe" opacity="0.9" />
      <rect x={rightStart} y="70" width={680 - rightStart} height="88" fill="#dbeafe" opacity="0.9" />
      {[86, 104, 122, 140].map((yy) => <line key={yy} x1="78" x2="672" y1={yy} y2={yy} stroke="#2563eb" strokeWidth="3" />)}
      {Array.from({ length: 22 }).map((_, index) => { const xx = 80 + index * 28; const dense = xx <= leftEnd || xx >= rightStart; return <rect key={xx} x={xx} y="76" width="1" height="76" fill={dense ? '#7c3aed' : '#a78bfa'} />; })}
      <line x1={leftEnd} x2={leftEnd} y1="60" y2="170" stroke="#64748b" strokeDasharray="4 3" />
      <line x1={rightStart} x2={rightStart} y1="60" y2="170" stroke="#64748b" strokeDasharray="4 3" />
      <text x="80" y="48" fontSize="11" fill="#0f172a">端部加密区 {String(row.endZones?.token ?? '-')} / L={num(row.endZones?.lengthM).toFixed(2)}m</text>
      <text x="288" y="48" fontSize="11" fill="#0f172a">跨中区 {String(row.middleZone?.token ?? '-')}</text>
      <text x="470" y="48" fontSize="11" fill="#0f172a">端部加密区</text>
      <text x="70" y="190" fontSize="11" fill="#334155">纵筋 {String(row.longitudinal?.token ?? '-')} · N={num(row.axialForceDesignKn).toFixed(0)} kN · 利用率 {num(row.utilization).toFixed(3)}</text>
      <text x="70" y="210" fontSize="10" fill="#64748b">搭接区避开端部刚域，采用错开搭接或经核准的机械连接；详见 R-04、D-01、D-07。</text>
    </svg>
  </div>;
}
