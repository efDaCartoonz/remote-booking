/**
 * Omnidesk Parent Integration Entry Script
 * Embeddable script for Omnidesk parent interface.
 * Securely extracts ticket number and DOM case_id and bridges them to the child Frame iframe.
 */
(function () {
  'use strict';

  function extractTicketNumber(url) {
    if (!url) return null;
    var match = url.match(/(?:^|\/)(?:user\/)?cases\/record\/([^/?#]+)/i);
    if (!match || !match[1]) return null;
    var candidate = decodeURIComponent(match[1]).trim();
    if (/^[a-zA-Z0-9_\-\.]+$/.test(candidate)) {
      return candidate;
    }
    return null;
  }

  function extractCaseId(doc) {
    if (!doc) doc = document;

    var elDataCaseId = doc.querySelector('[data-case-id]');
    if (elDataCaseId) {
      var val = (elDataCaseId.getAttribute('data-case-id') || '').trim();
      if (/^\d+$/.test(val)) return val;
    }

    var inputCaseId = doc.querySelector('input[name="case_id"]');
    if (inputCaseId && inputCaseId.value) {
      var val = inputCaseId.value.trim();
      if (/^\d+$/.test(val)) return val;
    }

    var elDataCase = doc.querySelector('[data-case]');
    if (elDataCase) {
      var val = (elDataCase.getAttribute('data-case') || '').trim();
      if (/^\d+$/.test(val)) return val;
    }

    var elIdCase = doc.getElementById('case_id');
    if (elIdCase) {
      var val = (elIdCase.value || elIdCase.getAttribute('data-id') || elIdCase.textContent || '').trim();
      if (/^\d+$/.test(val)) return val;
    }

    var elRecordId = doc.querySelector('[data-record-id]');
    if (elRecordId) {
      var val = (elRecordId.getAttribute('data-record-id') || '').trim();
      if (/^\d+$/.test(val)) return val;
    }

    return null;
  }

  function initRdmFrame(options) {
    options = options || {};
    var script = document.currentScript;
    var frameOrigin = options.frameOrigin || (script && script.src ? new URL(script.src).origin : null);
    if (!frameOrigin || !/^https?:\/\//.test(frameOrigin) || new URL(frameOrigin).origin !== frameOrigin) {
      throw new Error('A trusted RDM frameOrigin is required');
    }
    var container = options.container || document.querySelector('#rdm-frame-container') || document.body;

    var ticketNumber = extractTicketNumber(window.location.pathname) || extractTicketNumber(window.location.href);
    var caseId = extractCaseId(document);

    var iframe = options.iframe || document.querySelector('iframe[data-rdm-frame]');
    if (!iframe && options.autoCreateIframe !== false) {
      iframe = document.createElement('iframe');
      iframe.setAttribute('data-rdm-frame', 'true');
      iframe.setAttribute('title', 'RDM Booking Frame');
      iframe.src = frameOrigin + '/frame';
      iframe.style.width = '100%';
      iframe.style.minHeight = '480px';
      iframe.style.border = 'none';
      if (container) {
        container.appendChild(iframe);
      }
    }

    function sendInit() {
      if (!iframe || !iframe.contentWindow) return;
      if (!ticketNumber || !caseId) return;

      iframe.contentWindow.postMessage(
        {
          type: 'RDM_FRAME_INIT',
          payload: {
            omnidesk_ticket_number: ticketNumber,
            case_id: caseId
          }
        },
        frameOrigin
      );
    }

    function onMessage(event) {
      if (!iframe || event.source !== iframe.contentWindow || event.origin !== frameOrigin) return;

      if (event.data && event.data.type === 'RDM_FRAME_READY') {
        sendInit();
      }
    }

    window.addEventListener('message', onMessage);

    if (iframe) {
      iframe.addEventListener('load', function () {
        sendInit();
      });
    }

    return {
      sendInit: sendInit,
      destroy: function () {
        window.removeEventListener('message', onMessage);
      }
    };
  }

  // Export to global scope
  window.RdmOmnideskIntegration = {
    extractTicketNumber: extractTicketNumber,
    extractCaseId: extractCaseId,
    init: initRdmFrame
  };

  // Auto-init if data-auto-init attribute is present on script tag
  var currentScript = document.currentScript;
  if (currentScript && currentScript.getAttribute('data-auto-init') === 'true') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () {
        initRdmFrame();
      });
    } else {
      initRdmFrame();
    }
  }
})();
