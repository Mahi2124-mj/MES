const express = require('express');
const cors = require('cors');
const fs = require('fs');
const path = require('path');
const XLSX = require('xlsx');

const app = express();
const PORT = 5000;

app.use(cors());
app.use(express.json());

const DATA_DIR = path.join(__dirname, 'data');
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

function readData(filename) {
  const filepath = path.join(DATA_DIR, filename);
  if (!fs.existsSync(filepath)) return [];
  try { return JSON.parse(fs.readFileSync(filepath, 'utf8')); } catch { return []; }
}

function writeData(filename, data) {
  fs.writeFileSync(path.join(DATA_DIR, filename), JSON.stringify(data, null, 2));
}

// ─── MODELS ───────────────────────────────────────────────
app.get('/api/models', (req, res) => res.json(readData('models.json')));

app.post('/api/models', (req, res) => {
  const models = readData('models.json');
  const item = { id: Date.now(), ...req.body };
  models.push(item);
  writeData('models.json', models);
  res.json(item);
});

app.put('/api/models/:id', (req, res) => {
  const models = readData('models.json');
  const idx = models.findIndex(m => m.id == req.params.id);
  if (idx === -1) return res.status(404).json({ error: 'Not found' });
  models[idx] = { ...models[idx], ...req.body };
  writeData('models.json', models);
  res.json(models[idx]);
});

app.delete('/api/models/:id', (req, res) => {
  writeData('models.json', readData('models.json').filter(m => m.id != req.params.id));
  res.json({ success: true });
});

// ─── POKA YOKES ───────────────────────────────────────────
app.get('/api/pokayokes', (req, res) => res.json(readData('pokayokes.json')));

app.post('/api/pokayokes', (req, res) => {
  const list = readData('pokayokes.json');
  const item = { id: Date.now(), ...req.body };
  list.push(item);
  writeData('pokayokes.json', list);
  res.json(item);
});

app.put('/api/pokayokes/:id', (req, res) => {
  const list = readData('pokayokes.json');
  const idx = list.findIndex(p => p.id == req.params.id);
  if (idx === -1) return res.status(404).json({ error: 'Not found' });
  list[idx] = { ...list[idx], ...req.body };
  writeData('pokayokes.json', list);
  res.json(list[idx]);
});

app.delete('/api/pokayokes/:id', (req, res) => {
  writeData('pokayokes.json', readData('pokayokes.json').filter(p => p.id != req.params.id));
  res.json({ success: true });
});

// ─── ASSIGNMENTS (CONFIG) ─────────────────────────────────
app.get('/api/assignments', (req, res) => res.json(readData('assignments.json')));

app.post('/api/assignments', (req, res) => {
  const list = readData('assignments.json');
  const item = { id: Date.now(), ...req.body };
  list.push(item);
  writeData('assignments.json', list);
  res.json(item);
});

app.delete('/api/assignments/:id', (req, res) => {
  writeData('assignments.json', readData('assignments.json').filter(a => a.id != req.params.id));
  res.json({ success: true });
});

// ─── LINES ────────────────────────────────────────────────
app.get('/api/lines', (req, res) => res.json(readData('lines.json')));

app.post('/api/lines', (req, res) => {
  const list = readData('lines.json');
  const item = { id: Date.now(), ...req.body };
  list.push(item);
  writeData('lines.json', list);
  res.json(item);
});

app.put('/api/lines/:id', (req, res) => {
  const list = readData('lines.json');
  const idx = list.findIndex(l => l.id == req.params.id);
  if (idx === -1) return res.status(404).json({ error: 'Not found' });
  list[idx] = { ...list[idx], ...req.body };
  writeData('lines.json', list);
  res.json(list[idx]);
});

app.delete('/api/lines/:id', (req, res) => {
  writeData('lines.json', readData('lines.json').filter(l => l.id != req.params.id));
  res.json({ success: true });
});

// ─── LINE CONFIGS ──────────────────────────────────────────
app.get('/api/lineconfigs', (req, res) => res.json(readData('lineconfigs.json')));

app.post('/api/lineconfigs', (req, res) => {
  const list = readData('lineconfigs.json');
  const item = { id: Date.now(), ...req.body };
  list.push(item);
  writeData('lineconfigs.json', list);
  res.json(item);
});

app.delete('/api/lineconfigs/:id', (req, res) => {
  writeData('lineconfigs.json', readData('lineconfigs.json').filter(l => l.id != req.params.id));
  res.json({ success: true });
});

// ─── IMPORT FROM EXCEL ────────────────────────────────────
app.post('/api/import', (req, res) => {
  try {
    const excelPath = req.body.path || 'C:/Users/vivek.kumar/Desktop/poka yoka metrix.xlsx';
    const wb = XLSX.readFile(excelPath);

    // MODEL MASTER
    const modelSheet = wb.Sheets['MODEL MASTER'];
    const modelRows = XLSX.utils.sheet_to_json(modelSheet);
    const models = modelRows.map((row, i) => ({
      id: i + 1,
      modelName: String(row['Model Name'] || '').trim(),
      type:      String(row['type'] || '').trim(),
      oldModelNo: String(row['Old Model No'] || '').trim(),
      model:     String(row['model'] || '').trim()
    })).filter(m => m.modelName);
    writeData('models.json', models);

    // FINAL SEAT → poka yokes + assignments
    const finalSheet = wb.Sheets['final seat'];
    const finalRows = XLSX.utils.sheet_to_json(finalSheet);

    // Unique poka yokes — separate per (pyNo + typeSide) so LH and RH are distinct
    const pyMap = new Map();
    finalRows.forEach(row => {
      const pyNo    = String(row['Poka Yoke No'] || '').trim();
      const typeSide = String(row['Type\nSide']  || '').trim();
      const key = `${pyNo}||${typeSide}`;
      if (pyNo && !pyMap.has(key)) {
        pyMap.set(key, {
          id: pyMap.size + 1,
          pyNo,
          description:    String(row['Poka Yoke Name']       || '').trim(),
          modelType:      String(row['Model Type']           || '').trim(),
          typeSide,
          dBit:           String(row['D bit From PLC']       || '').trim(),
          desiredValue:   row['Desired Value\n(0/1/2)'] ?? '',
          machineFixture: String(row['Machine/Fixture']      || '').trim()
        });
      }
    });
    writeData('pokayokes.json', Array.from(pyMap.values()));

    // Assignments
    const assignments = finalRows.map((row, i) => ({
      id: i + 1,
      pyNo:          String(row['Poka Yoke No'] || '').trim(),
      pyName:        String(row['Poka Yoke Name'] || '').trim(),
      typeSide:      String(row['Type\nSide'] || '').trim(),
      modelType:     String(row['Model Type'] || '').trim(),
      modelName:     String(row['Model Name'] || '').trim(),
      type2:         String(row['Type2'] || '').trim(),
      oldModelNo:    String(row['Old Model No'] || '').trim(),
      modelSeries:   String(row['Model'] || '').trim(),
      dBit:          String(row['D bit From PLC'] || '').trim(),
      desiredValue:  row['Desired Value\n(0/1/2)'] ?? '',
      machineFixture: String(row['Machine/Fixture'] || '').trim()
    })).filter(a => a.pyNo && a.modelName);
    writeData('assignments.json', assignments);

    res.json({
      success: true,
      imported: { models: models.length, pokayokes: pyMap.size, assignments: assignments.length }
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── EXPORT TO EXCEL ──────────────────────────────────────
app.get('/api/export', (req, res) => {
  try {
    const models      = readData('models.json');
    const pokayokes   = readData('pokayokes.json');
    const assignments = readData('assignments.json');

    const wb = XLSX.utils.book_new();

    XLSX.utils.book_append_sheet(wb,
      XLSX.utils.json_to_sheet(models.map(m => ({
        'Model Name': m.modelName, 'type': m.type,
        'Old Model No': m.oldModelNo, 'model': m.model
      }))), 'MODEL MASTER');

    XLSX.utils.book_append_sheet(wb,
      XLSX.utils.json_to_sheet(pokayokes.map(p => ({
        'Poka Yoke No': p.pyNo, 'Poka Yoke Name': p.description,
        'Model Type': p.modelType, 'Machine/Fixture': p.machineFixture
      }))), 'POKA YOKE MASTER');

    XLSX.utils.book_append_sheet(wb,
      XLSX.utils.json_to_sheet(assignments.map((a, i) => ({
        'ID': i + 1,
        'Poka Yoke No': a.pyNo, 'Poka Yoke Name': a.pyName,
        'Type Side': a.typeSide, 'Model Type': a.modelType,
        'Model Name': a.modelName, 'Type2': a.type2,
        'Old Model No': a.oldModelNo, 'Model': a.modelSeries,
        'D bit From PLC': a.dBit,
        'Desired Value (0/1/2)': a.desiredValue,
        'Machine/Fixture': a.machineFixture
      }))), 'final seat');

    const exportPath = path.join(DATA_DIR, 'poka_yoke_export.xlsx');
    XLSX.writeFile(wb, exportPath);
    res.download(exportPath, 'poka_yoke_matrix.xlsx');
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const server = app.listen(PORT, () => console.log(`Server: http://localhost:${PORT}`));
server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`\n⚠️  Port ${PORT} already in use!`);
    console.error('Fix: Run this command in a new terminal:');
    console.error(`   for /f "tokens=5" %a in ('netstat -aon ^| findstr :${PORT} ^| findstr LISTENING') do taskkill /PID %a /F\n`);
    process.exit(1);
  }
});
