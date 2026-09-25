/**
 * Bounded graph view.
 *
 * Node kinds are distinguished by shape as well as colour, and edge provenance by line
 * style: solid for relationships read from the source records, dashed for anything the
 * product inferred. The transaction node always sits between addresses - no direct
 * address-to-address payment is ever drawn, because the data cannot support one.
 *
 * A keyboard-accessible table of the same nodes is provided alongside the canvas.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import type { Core, ElementDefinition } from 'cytoscape'
import { shortId } from '../api'

const NODE_STYLE: Record<string, { shape: string; colour: string; label: string }> = {
  address: { shape: 'ellipse', colour: 'var(--node-address)', label: 'Address' },
  transaction: { shape: 'round-rectangle', colour: 'var(--node-transaction)', label: 'Transaction' },
  endpoint: { shape: 'diamond', colour: 'var(--node-endpoint)', label: 'Network endpoint' },
}

const EDGE_STYLE: Record<string, { colour: string; dashed: boolean; label: string }> = {
  spend: { colour: '#5a6a7d', dashed: false, label: 'Supplied: address spent into transaction' },
  receive: { colour: '#5a6a7d', dashed: false, label: 'Supplied: transaction paid address' },
  observation: { colour: '#9aa7b5', dashed: true, label: 'Network sighting (not ownership)' },
  inferred_continuity: { colour: '#7a5199', dashed: true, label: 'Inferred continuity (no prevouts)' },
  candidate_common_control: { colour: '#b54708', dashed: true, label: 'Inferred common control' },
}

// Resolve CSS custom properties: Cytoscape renders to canvas and cannot read them.
function resolve(colour: string): string {
  if (!colour.startsWith('var(')) return colour
  const name = colour.slice(4, -1).trim()
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#1e4066'
}

export function GraphView({
  payload,
  selected,
  onSelect,
  loading,
}: {
  payload: any
  selected: string | null
  onSelect: (id: string, kind: string, raw: any) => void
  loading?: boolean
}) {
  const container = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Core | null>(null)
  const [showTable, setShowTable] = useState(false)

  const elements = useMemo<ElementDefinition[]>(() => {
    if (!payload) return []
    const nodes = (payload.nodes ?? []).map((node: any) => ({
      data: {
        id: node.id,
        kind: node.kind,
        label: node.kind === 'transaction' ? shortId(node.txid ?? node.id, 8, 4)
          : node.kind === 'endpoint' ? node.ip
          : shortId(node.address ?? node.id, 8, 4),
        alerted: node.alert ? node.alert.band : 'none',
        raw: node,
      },
    }))
    const edges = (payload.edges ?? []).map((edge: any) => ({
      data: {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        kind: edge.kind,
        inferred: edge.inferred ? 'yes' : 'no',
        raw: edge,
      },
    }))
    return [...nodes, ...edges]
  }, [payload])

  useEffect(() => {
    if (!container.current) return
    const cy = cytoscape({
      container: container.current,
      elements,
      wheelSensitivity: 0.25,
      style: [
        {
          selector: 'node',
          style: {
            'background-color': (el: any) => resolve(NODE_STYLE[el.data('kind')]?.colour ?? '#1e4066'),
            shape: (el: any) => (NODE_STYLE[el.data('kind')]?.shape ?? 'ellipse') as any,
            label: 'data(label)',
            'font-size': 8,
            'font-family': 'ui-monospace, monospace',
            color: '#17212f',
            'text-valign': 'bottom',
            'text-margin-y': 3,
            width: 20,
            height: 20,
            'border-width': 1,
            'border-color': '#ffffff',
          },
        },
        {
          selector: 'node[kind="transaction"]',
          style: { width: 16, height: 11 },
        },
        {
          // An alerted node is outlined AND its band is written in the table beside it.
          selector: 'node[alerted="high"]',
          style: { 'border-width': 3, 'border-color': resolve('var(--high)') },
        },
        {
          selector: 'node[alerted="medium"]',
          style: { 'border-width': 3, 'border-color': resolve('var(--medium)') },
        },
        {
          selector: 'node:selected',
          style: { 'border-width': 4, 'border-color': resolve('var(--accent)') },
        },
        {
          selector: 'edge',
          style: {
            width: 1.2,
            'line-color': (el: any) => resolve(EDGE_STYLE[el.data('kind')]?.colour ?? '#5a6a7d'),
            'line-style': (el: any) => (EDGE_STYLE[el.data('kind')]?.dashed ? 'dashed' : 'solid') as any,
            'curve-style': 'bezier',
            'target-arrow-shape': 'triangle',
            'target-arrow-color': (el: any) => resolve(EDGE_STYLE[el.data('kind')]?.colour ?? '#5a6a7d'),
            'arrow-scale': 0.6,
            opacity: 0.85,
          },
        },
      ],
      layout: { name: 'cose', animate: false, nodeRepulsion: 9000, idealEdgeLength: 55,
        padding: 24, randomize: false } as any,
    })

    cy.on('tap', 'node', (event) => {
      const node = event.target
      onSelect(node.id(), node.data('kind'), node.data('raw'))
    })
    cyRef.current = cy
    return () => {
      cy.destroy()
      cyRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [elements])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy || !selected) return
    cy.elements().unselect()
    const node = cy.getElementById(selected)
    if (node && node.length) {
      node.select()
      cy.animate({ center: { eles: node } }, { duration: 200 })
    }
  }, [selected, elements])

  const meta = payload?.meta
  const nodes = payload?.nodes ?? []

  return (
    <>
      <div className="graph-canvas">
        <div ref={container} style={{ width: '100%', height: '100%' }} />
        {loading ? (
          <div className="overlay">
            <span className="spinner" aria-hidden="true" />
          </div>
        ) : null}
        {!loading && nodes.length === 0 ? (
          <div className="overlay">
            <p style={{ color: 'var(--text-muted)' }}>
              No nodes to display. Select an alert or search for an address.
            </p>
          </div>
        ) : null}
      </div>

      <div className="legend">
        {Object.entries(NODE_STYLE).map(([kind, style]) => (
          <span className="legend-item" key={kind}>
            <svg className="legend-swatch" viewBox="0 0 14 14" aria-hidden="true">
              {style.shape === 'ellipse' ? (
                <circle cx="7" cy="7" r="5" fill={resolve(style.colour)} />
              ) : style.shape === 'diamond' ? (
                <polygon points="7,1 13,7 7,13 1,7" fill={resolve(style.colour)} />
              ) : (
                <rect x="1" y="4" width="12" height="6" rx="1.5" fill={resolve(style.colour)} />
              )}
            </svg>
            {style.label}
          </span>
        ))}
        {Object.entries(EDGE_STYLE).map(([kind, style]) => (
          <span className="legend-item" key={kind}>
            <svg className="legend-swatch" viewBox="0 0 14 14" aria-hidden="true">
              <line x1="0" y1="7" x2="14" y2="7" stroke={resolve(style.colour)} strokeWidth="2"
                strokeDasharray={style.dashed ? '3 2' : undefined} />
            </svg>
            {style.label}
          </span>
        ))}
      </div>

      <div className="pager">
        <span>
          {meta
            ? `${meta.nodes_returned} nodes, ${meta.edges_returned} edges · ${meta.hops_reached} hop(s) of ${meta.hops_requested} · budget ${meta.node_budget}`
            : 'No view loaded'}
          {meta?.truncated ? (
            <strong style={{ color: 'var(--medium)' }}>
              {' '}· View truncated at the node budget — expand explicitly to see more.
            </strong>
          ) : null}
        </span>
        <button className="btn btn-sm" onClick={() => setShowTable((v) => !v)}
          aria-expanded={showTable}>
          {showTable ? 'Hide' : 'Show'} table view
        </button>
      </div>

      {showTable ? (
        <div className="table-wrap" style={{ maxHeight: 240, overflowY: 'auto',
          borderTop: '1px solid var(--line)' }}>
          <table className="data">
            <caption className="sr-only">
              Keyboard-accessible table of the nodes currently shown in the graph
            </caption>
            <thead>
              <tr>
                <th>Kind</th>
                <th>Identifier</th>
                <th>Alert</th>
                <th>Anomaly percentile</th>
              </tr>
            </thead>
            <tbody>
              {nodes.map((node: any) => (
                <tr key={node.id} className="selectable"
                  onClick={() => onSelect(node.id, node.kind, node)}>
                  <td>{NODE_STYLE[node.kind]?.label ?? node.kind}</td>
                  <td className="mono">{node.address ?? node.txid ?? node.ip ?? node.id}</td>
                  <td>{node.alert ? `${node.alert.band} (${node.alert.pattern})` : '—'}</td>
                  <td className="num">
                    {node.score?.anomaly_percentile != null
                      ? node.score.anomaly_percentile.toFixed(1)
                      : node.kind === 'address' ? 'unscored' : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  )
}
