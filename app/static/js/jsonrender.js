// Generative UI with Vercel's json-render (github.com/vercel-labs/json-render, v0.21.0, Apache-2.0):
// a catalog of card components; specs ({root, elements}) come from Gemini as SpecStream patches (live boards)
// or are built from live Wikidata / UMLS data (concept cards) and guideline tables (excerpts).
import React from 'react';
import {createRoot} from 'react-dom/client';
import {defineCatalog, createSpecStreamCompiler} from '@json-render/core';
import {schema} from '@json-render/react/schema';
import {defineRegistry, Renderer, JSONUIProvider} from '@json-render/react';
import {z} from 'zod';
const h = React.createElement, any = z.record(z.string(), z.any());
const C = d => ({props: any, description: d});
const catalog = defineCatalog(schema, {components: {
  Board: C('Container with a short title'), Point: C('Key point with icon, title, text'), Stat: C('Short value with label'),
  Warning: C('Caution'), Chips: C('Labelled list of short items'), ConceptCard: C('Concept header with image and identifiers'),
  IdList: C('Identifier list'), RelationList: C('Labelled relation chips'), Excerpt: C('Guideline table excerpt'), Quote: C('Verbatim guideline quote with source'), Note: C('Small note')}, actions: {}});
const Icon = ({name}) => h('span', {className: 'jr-ic', style: {'--ic': `url(${window.jrIcon(name)})`}});
const {registry} = defineRegistry(catalog, {components: {
  Board: ({props, children}) => h('section', {className: 'jr-board'}, props.title ? h('header', null, h('span', {className: 'jr-spark'}), props.title) : null, h('div', {className: 'jr-items'}, children)),
  Point: ({props}) => h('div', {className: 'jr-point'}, h(Icon, {name: props.icon}), h('div', null, h('b', null, props.title), props.text ? h('p', null, props.text) : null)),
  Stat: ({props}) => h('div', {className: 'jr-stat'}, h(Icon, {name: props.icon}), h('strong', null, props.value), h('span', null, props.label)),
  Warning: ({props}) => h('div', {className: 'jr-warn'}, h(Icon, {name: props.icon || 'triangle-alert'}), h('p', null, props.text)),
  Chips: ({props}) => h('div', {className: 'jr-chips'}, props.label ? h('span', {className: 'jr-lab'}, props.label) : null, (props.items || []).map((t, i) => h('span', {key: i, className: 'jr-chip'}, String(t)))),
  ConceptCard: ({props, children}) => window.jrHTML.concept(h, props, children),
  IdList: ({props}) => window.jrHTML.ids(h, props),
  RelationList: ({props}) => window.jrHTML.relations(h, props),
  Excerpt: ({props}) => window.jrHTML.excerpt(h, props),
  Quote: ({props}) => window.jrHTML.quote(h, props),
  Note: ({props}) => h('p', {className: 'jr-note'}, props.text)}});
const Fallback = ({element}) => null;
window.JR = {
  ready: true,
  mount(el) {
    const root = createRoot(el); let spec = null, loading = false;
    const draw = () => root.render(h(JSONUIProvider, {registry}, h(Renderer, {spec, registry, loading, fallback: Fallback})));
    return {set(s, l) { spec = s ? JSON.parse(JSON.stringify(s)) : null; loading = !!l; draw(); }, unmount() { root.unmount(); }};
  },
  compiler: () => createSpecStreamCompiler({root: '', elements: {}})
};
window.dispatchEvent(new Event('jr-ready'));
