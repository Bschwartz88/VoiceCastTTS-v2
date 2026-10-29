// Exercise the studio's actual casting functions without launching a browser.
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {test} = require('node:test');
const source = readFileSync(resolve(__dirname, '../../web/static/studio.js'), 'utf8');
const casting = source.slice(source.indexOf('const EVERYDAY_FOLDER ='), source.indexOf('function manualDetectSpeakers('));
const ai = source.slice(source.indexOf('const AI_TYPE_VOICES ='), source.indexOf('function showAiDiffs('));
function fixture() {
  const context = vm.createContext({});
  vm.runInContext(`
    let appConfig = {available_popular_voices: ['am_eric','am_adam','bm_daniel','am_liam','am_puck','af_bella','af_sky'].map(id => ({id}))};
    let userSettings = {voice_folders:{}};
    let characters = [], nextCharIndex = 1, script = '';
    const document = {getElementById: id => id === 'scriptInput' ? {value:script} : null};
    function cleanSpeakerTag(name) { return {name}; }
    function voiceForSpeakerName(name) { return name === 'Alias' ? 'am_liam' : null; }
    function renderCharacterCards() {}
    function renderDynamicToolbarTags() {}
    function setSpeed() {}
    function setShown() {}
  ` + casting + ai, context);
  return code => JSON.parse(vm.runInContext(`JSON.stringify(${code})`, context));
}
test('new male characters prefer Eric through direct and AI casting', () => {
  const run = fixture();
  assert.equal(run('guessGenderAndVoice("John",0).voice'), 'am_eric');
  assert.equal(run('voiceForType("Male",0,new Set()).voice'), 'am_eric');
  assert.equal(run('guessGenderAndVoice("Speaker 2",1).voice'), 'am_eric');
});
test('another male uses a distinct available voice', () => {
  const run = fixture();
  assert.notEqual(run('guessGenderAndVoice("Bob",1,new Set(["am_eric"])).voice'), 'am_eric');
});
test('unavailable Eric is skipped in both typed casting paths', () => {
  const run = fixture();
  run('(appConfig.available_popular_voices = [{id:"am_liam"}], true)');
  assert.equal(run('voiceForType("Male",0,new Set()).voice'), 'am_liam');
  assert.equal(run('guessGenderAndVoice("John",0).voice'), 'am_liam');
});
test('owner Everyday and named-role voices take priority, retaining speed', () => {
  const run = fixture();
  run('(appConfig.available_popular_voices.push({id:"Dad",voice_type:"Male",start_speed:.91}), userSettings.voice_folders.Dad="Everyday people", true)');
  assert.deepEqual(run('voiceForType("Male",0,new Set())'), {gender:'Male',voice:'Dad',speed:.91});
  assert.equal(run('guessGenderAndVoice("Father",0).voice'), 'Dad');
});
test('saved assignments survive detection and AI cast suggestions', () => {
  const run = fixture();
  run('(characters=[{id:"saved",name:"John",gender:"Male",voice:"am_puck",speed:.88}],script="[John] Hi.\\n[Bob] Hello.",true)');
  run('(applyAiCast([{name:"John",voice_type:"Male"},{name:"Bob",voice_type:"Male"}],new Set(["john"])),true)');
  assert.deepEqual(run('characters.map(c=>[c.name,c.voice,c.speed])'), [['John','am_puck',.88],['Bob','am_eric',1]]);
});
test('named voice aliases still override the new-character suggestion', () => {
  const run = fixture();
  run('(script="[Alias] Hello.",applyAiCast([{name:"Alias",voice_type:"Male"}],new Set()),true)');
  assert.equal(run('characters[0].voice'), 'am_liam');
});
test('female and child first choices remain unchanged', () => {
  const run = fixture();
  assert.equal(run('voiceForType("Female",0,new Set()).voice'), 'af_bella');
  assert.equal(run('voiceForType("Child",0,new Set()).voice'), 'af_sky');
});

test('whole-name hints avoid substring guesses and untyped professions',()=>{
 const run=fixture();
 for(const name of ['Tomlinson','Emmaus','Madam','Sheriff','Doctor','Young adult'])assert.equal(run(`speakerNameType(${JSON.stringify(name)})`),null,name);
 assert.equal(run('speakerNameType("Mr. Hale")'),'Male');
 assert.equal(run('speakerNameType("Mrs. Hale")'),'Female');
 assert.equal(run('speakerNameType("little Timmy")'),'Child');
 assert.equal(run('speakerNameType("young woman")'),'Female');
});
test('Neutral model type yields to honorific while saved choices remain',()=>{
 const run=fixture();
 run('(script="[Mr. Hale] Hello.\\n[Mrs. Hale] Hi.",applyAiCast([{name:"Mr. Hale",voice_type:"Neutral"},{name:"Mrs. Hale",voice_type:"Neutral"}],new Set()),true)');
 assert.deepEqual(run('characters.map(c=>[c.gender,c.voice])'),[['Male','am_eric'],['Female','af_bella']]);
});
test('unknown speakers prefer different available voices',()=>{
 const run=fixture();
 assert.notEqual(run('guessGenderAndVoice("Speaker 2",1,new Set(["am_eric"])).voice'),'am_eric');
});
