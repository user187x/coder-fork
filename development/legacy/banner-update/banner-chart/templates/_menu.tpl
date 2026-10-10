{{/*
Rewrites for Coder's entry JavaScript chunk (assets/index-<hash>.js), which renders the user-menu item
"Codernauts" (a react-router Link to /coder-cup). The dashboard is client-rendered, so this text exists
in no HTML body; the bundle is the only place it can be changed.

The bundle is minified, so identifiers (V, YB, U, pc ...) change between Coder builds. Both regexes are
therefore anchored on things a minifier keeps (property names and string literals) and CAPTURE the
identifiers. If Coder changes the code so a regex stops matching, nothing is rewritten and the menu is
simply left as Coder ships it. Check with ../verify-menu after upgrading Coder.

Tested against Coder v2.36.0 (a300a06).  Go RE2 syntax: `${n}` are capture groups.
*/}}

{{/* 1. Inside the user-menu component, compute whether the signed-in user is an admin. */}}
{{- define "coder-banner.menu.adminRegex" -}}
\(\{user:(\w+),buildInfo:(\w+),profileExtra:(\w+),supportLinks:(\w+),onSignOut:(\w+)\}\)=>\{
{{- end -}}
{{- define "coder-banner.menu.adminReplacement" -}}
({user:${1},buildInfo:${2},profileExtra:${3},supportLinks:${4},onSignOut:${5}})=>{let __bannerAdmin=(${1}.roles||[]).some(r=>{{ .Values.admin.roles | toJson }}.includes(r.name));
{{- end -}}

{{/* 2. Replace the Codernauts item with a plain link to the admin page. */}}
{{- define "coder-banner.menu.itemRegex" -}}
(\w+)\((\w+),\{asChild:!0,children:(\w+)\(\w+,\{to:`/coder-cup`,children:\[.*?(\w+)\(`span`,\{children:`Codernauts`\}\)\]\}\)\}\)
{{- end -}}
{{- define "coder-banner.menu.itemReplacement" -}}
{{- if .Values.menu.adminOnly -}}(typeof __bannerAdmin===`undefined`||__bannerAdmin)&&{{- end -}}
${1}(${2},{asChild:!0,children:${3}(`a`,{href:`/__banner/admin`,children:[${3}(`svg`,{viewBox:`0 0 24 24`,fill:`none`,stroke:`currentColor`,strokeWidth:`1.5`,strokeLinecap:`round`,strokeLinejoin:`round`,xmlns:`http://www.w3.org/2000/svg`,children:[${4}(`path`,{d:`m3 11 18-5v12L3 14v-3z`}),${4}(`path`,{d:`M11.6 16.8a3 3 0 1 1-5.8-1.6`})]}),${4}(`span`,{children:`{{ .Values.menu.label }}`})]})})
{{- end -}}
