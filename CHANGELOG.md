# Novità

Tutte le modifiche rilevanti di Plenitude per Home Assistant.

## [0.2.0] - 2026-10-19

### Novità

- Logo e icona Plenitude nell'elenco delle integrazioni (tema chiaro e scuro).
- La cartella delle bollette viene creata da sola al primo avvio: l'integrazione si
  carica subito e attende i PDF, invece di restare in errore.

## [0.1.0] - 2026-10-19

- Prima versione. Legge le bollette gas Eni Plenitude in PDF da una cartella e ne ricava
  importo, scadenza, consumi e prezzi. Se collegata a una fornitura MyItalgas, crea lì i
  periodi tariffari del gas per la stima dei costi nel pannello Energia.
- L'accesso diretto all'app Plenitude non è incluso: il modulo `api.py` ne lascia
  l'interfaccia pronta da completare.
