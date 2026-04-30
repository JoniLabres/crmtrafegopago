# Guia de Setup GTM — trafego-pago

## Pré-requisitos

- Acesso ao GTM com permissão de Publicar
- IDs disponíveis: GA4 Measurement ID, Meta Pixel ID, LinkedIn Partner ID, TikTok Pixel ID

---

## 1. Importar o Container

1. Acesse [tagmanager.google.com](https://tagmanager.google.com)
2. Selecione sua **Conta** e o **Container** desejado
3. No menu lateral: **Admin → Importar Container**
4. Clique em **Escolher arquivo** e selecione `tracking/gtm_config.json`
5. Em *Selecionar workspace*: escolha **Novo workspace** → nome: `trafego-pago v1.0`
6. Em *Opção de importação*: escolha **Mesclar → Renomear conflitos**
7. Clique em **Confirmar**

---

## 2. Substituir os IDs de Constantes

Após a importação, atualize as variáveis com seus IDs reais:

| Variável no GTM | Onde encontrar |
|---|---|
| `Const - GA4 Measurement ID` | GA4 → Admin → Fluxos de dados → seu fluxo → ID de medição |
| `Const - Meta Pixel ID` | Meta Business → Gerenciador de Eventos → Pixels → ID do Pixel |
| `Const - LinkedIn Partner ID` | LinkedIn Campaign Manager → Conta → Insight Tag → Partner ID |
| `Const - TikTok Pixel ID` | TikTok Ads Manager → Ativos → Eventos → Pixel ID |

**Como editar:**
1. Menu lateral → **Variáveis**
2. Clique na variável `Const - GA4 Measurement ID`
3. Substitua `{{GA4_MEASUREMENT_ID}}` pelo ID real (ex: `G-XXXXXXXXXX`)
4. Repita para as outras 3 constantes

---

## 3. Validar no Preview Mode

### Ativar o Preview
1. Clique no botão **Visualizar** (canto superior direito)
2. Digite a URL do seu site e clique em **Connect**
3. O GTM Debug Panel abrirá no seu site

### Checklist de validação por tag

#### GA4 - Configuration
- [ ] Dispara em todas as páginas (trigger: Pageview - All Pages)
- [ ] No GA4 Realtime: verificar que sessões aparecem

#### GA4 - Event lead_generated
- [ ] Disparar o evento via console: `dataLayer.push({event: 'lead_generated', form_id: 'teste', lead_value: 100})`
- [ ] No Debug Panel: verificar parâmetros UTM preenchidos
- [ ] No GA4 Realtime → Eventos: confirmar `lead_generated`

#### GA4 - Event purchase_completed
- [ ] Disparar: `dataLayer.push({event: 'purchase_completed', lead_value: 1500})`
- [ ] Verificar evento `purchase` no GA4

#### Meta Pixel - PageView
- [ ] Instalar extensão [Meta Pixel Helper](https://chromewebstore.google.com/detail/meta-pixel-helper/fdgfkebogiimcoedlicjlajpkdmockpc)
- [ ] Verificar PageView na extensão ao carregar qualquer página

#### Meta Pixel - Lead
- [ ] Disparar `lead_generated` via dataLayer
- [ ] Verificar evento `Lead` na extensão Meta Pixel Helper
- [ ] Confirmar que `eventID` está presente (para deduplicação com CAPI)

#### LinkedIn Insight Tag
- [ ] Instalar extensão [LinkedIn Insight Tag Helper](https://chromewebstore.google.com/detail/linkedin-insight-tag-help/fljkadmjcliefejdcgobolfnjpfoakda)
- [ ] Verificar que a tag dispara em todas as páginas

#### TikTok Pixel - PageView
- [ ] Instalar extensão [TikTok Pixel Helper](https://chromewebstore.google.com/detail/tiktok-pixel-helper/aelgobmabdmlfmfabialempdnkhjdpng)
- [ ] Verificar `PageView` na extensão ao carregar o site

#### TikTok Pixel - Lead
- [ ] Disparar `lead_generated` via dataLayer
- [ ] Verificar evento `SubmitForm` na extensão TikTok Pixel Helper

---

## 4. Testar UTMs no Preview

1. Acesse seu site com uma UTM de teste:
   ```
   https://seusite.com.br/lp?utm_source=meta&utm_medium=paid_social&utm_campaign=produto-a_topo_leads_2025-05&utm_content=video_dor_v1&utm_term=lookalike-clientes
   ```
2. No Debug Panel, clique em **Pageview**
3. Vá em **Variables** e confirme:
   - `URL - utm_source` = `meta`
   - `URL - utm_medium` = `paid_social`
   - `URL - utm_campaign` = `produto-a_topo_leads_2025-05`
   - `URL - utm_content` = `video_dor_v1`
   - `URL - utm_term` = `lookalike-clientes`

---

## 5. Publicar

1. Feche o Preview
2. Clique em **Enviar** (canto superior direito)
3. Em *Versão*: nome `v1.0 - trafego-pago setup inicial`
4. Clique em **Publicar**

---

## 6. Configurar o dataLayer no seu site

Adicione este bloco **antes** da tag GTM em todas as páginas:

```html
<script>
  window.dataLayer = window.dataLayer || [];
  dataLayer.push({
    'page_path': window.location.pathname
  });
</script>
```

Ao capturar um lead (submit do formulário), dispare:

```javascript
dataLayer.push({
  event: 'lead_generated',
  form_id: 'nome-do-formulario',
  lead_value: 0,
  event_id: 'lead_' + Date.now()  // ID único para deduplicação CAPI
});
```

Ao confirmar uma venda:

```javascript
dataLayer.push({
  event: 'purchase_completed',
  lead_value: 1500,
  currency: 'BRL'
});
```

---

## Troubleshooting

| Problema | Causa provável | Solução |
|---|---|---|
| Tag não dispara | Trigger incorreto | Verificar trigger no Debug Panel |
| UTMs aparecem vazias | UTM não está na URL | Confirmar URL com parâmetros UTM |
| Meta Pixel duplicado | Pixel já instalado no site | Remover pixel hardcoded do site |
| GA4 sem dados | ID de medição errado | Confirmar ID na variável Const |
