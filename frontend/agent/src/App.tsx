import { Authenticator } from '@aws-amplify/ui-react';
import '@aws-amplify/ui-react/styles.css';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import AgentSelectPage from './pages/AgentSelectPage';
import ChatPage from './pages/ChatPage';

function App() {
  return (
    <BrowserRouter>
      <Authenticator hideSignUp={true}>
        {({ signOut, user }) => (
          <Routes>
            <Route path="/" element={<AgentSelectPage signOut={signOut} user={user} />} />
            <Route path="/agent/:agentId" element={<ChatPage signOut={signOut} user={user} />} />
          </Routes>
        )}
      </Authenticator>
    </BrowserRouter>
  );
}

export default App;
